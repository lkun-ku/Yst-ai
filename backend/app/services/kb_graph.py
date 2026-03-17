"""路线③：LangGraph StateGraph 编排的知识库出题（跨文档范围出题）。

与路线②（kb_generate.py 手写控制流）**共享同一套**检索 / 提示词 / LLM·Embedding 接缝 /
校验落库逻辑，且复用其全部子步骤函数（_grade_and_filter / _rewrite_scope /
_selfcheck_batch / _generate_batch / _persist_questions 等），**唯一差异是编排层用
StateGraph 表达**：retrieve → grade →（不达标则 rewrite→retrieve） → assemble → generate。

这正是 LangGraph 相对手写控制流的真实价值：把「检索评分→改写重检索→生成」的
Self-RAG/CRAG 状态机显式化为带条件边的图，可观测、可 checkpoint、可扩展分支。
实验测量的是「框架 vs 手写编排」本身（控制变量：检索/提示词/接缝/校验全部一致）。

注意：节点直接调用 LLMClient（普通 Python 函数即可，无需包成 BaseChatModel）。
LLM 调用返回 None 时各子步骤自行降级，图整体不阻塞出题。
"""

from __future__ import annotations

import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..models import Question
from .doc_generate import _persist_questions, _trim_to_budget, build_batches, normalize_spec
from .kb_generate import (
    KB_RECALL_K,
    MAX_REGEN,
    MAX_REWRITE,
    _chunk_payloads,
    _generate_batch,
    _grade_and_filter,
    _rewrite_scope,
    _selfcheck_batch,
)
from .kb_retrieval import retrieve_by_scope

logger = logging.getLogger(__name__)


class KbState(TypedDict, total=False):
    db: object
    client: object
    candidate_id: int
    scope: str
    k: int
    difficulty: str
    focus: object
    spec: list
    embed_fn: object
    chunks: list
    graded: list
    sufficient: bool
    rewrites: int
    picked: list
    seen: object
    created: list
    done: int
    on_progress: object


# ---------------- 图节点（普通函数，返回状态增量） ----------------

def _n_retrieve(s: KbState) -> dict:
    chunks = retrieve_by_scope(
        s["db"], s["candidate_id"], s["scope"], k=s.get("k", KB_RECALL_K), embed_fn=s.get("embed_fn")
    )
    return {"chunks": chunks}


def _n_grade(s: KbState) -> dict:
    graded, sufficient = _grade_and_filter(s["client"], s["scope"], s["chunks"])
    return {"graded": graded, "sufficient": sufficient}


def _route_after_grade(s: KbState) -> str:
    if s.get("sufficient") or s.get("rewrites", 0) >= MAX_REWRITE:
        return "assemble"
    return "rewrite"


def _n_rewrite(s: KbState) -> dict:
    new_scope = _rewrite_scope(s["client"], s["scope"], "相关切片不足")
    if new_scope == s["scope"]:
        return {"rewrites": MAX_REWRITE}  # 改写无效 → 强制终止循环，避免死循环
    return {"scope": new_scope, "rewrites": s.get("rewrites", 0) + 1}


def _n_assemble(s: KbState) -> dict:
    graded = s.get("graded") or s["chunks"]
    picked = _trim_to_budget(_chunk_payloads(graded), settings.doc_max_input_chars)
    return {"picked": picked}


def _n_generate(s: KbState) -> dict:
    db = s["db"]
    client = s["client"]
    scope = s["scope"]
    spec = s["spec"]
    difficulty = s["difficulty"]
    focus = s.get("focus")
    picked = s["picked"]
    seen = s["seen"]
    on_progress = s.get("on_progress")

    created: list[Question] = list(s.get("created") or [])
    batches = build_batches(spec)
    total = sum(c for _, c in batches)
    done = s.get("done", 0)

    for qtype, count in batches:
        payloads = _generate_batch(client, scope, qtype, count, difficulty, focus, picked, seen)
        payloads, ok = _selfcheck_batch(client, payloads, s["chunks"])
        if not ok and MAX_REGEN > 0:
            regen = _generate_batch(
                client, scope, qtype, count, difficulty, focus, picked, seen,
                extra="请更严格忠于资料、避免幻觉与超纲",
            )
            regen, _ = _selfcheck_batch(client, regen, s["chunks"])
            payloads = regen or payloads
        created += _persist_questions(
            db, s["candidate_id"], None, payloads or [], seen,
            source_chunk=picked[0]["content"] if picked else None,
        )
        done += count
        if on_progress:
            on_progress(min(done, total), total)

    # 补偿：不足时按 spec 顺序补足（最多 3 轮，沿用 doc_generate 经验）
    attempts = 0
    while len(created) < total and attempts < 3:
        attempts += 1
        for qtype, count in spec:
            if len(created) >= total:
                break
            need = total - len(created)
            payloads = _generate_batch(
                client, scope, qtype, min(settings.doc_batch_size, need),
                difficulty, focus, picked, seen,
            )
            created += _persist_questions(
            db, s["candidate_id"], None, payloads or [], seen,
            source_chunk=picked[0]["content"] if picked else None,
        )
        if on_progress:
            on_progress(min(len(created), total), total)

    return {"created": created, "done": min(done, total), "seen": seen}


# ---------------- 图构建 ----------------

def _build_graph() -> "CompiledGraph":
    g = StateGraph(KbState)
    g.add_node("retrieve", _n_retrieve)
    g.add_node("grade", _n_grade)
    g.add_node("rewrite", _n_rewrite)
    g.add_node("assemble", _n_assemble)
    g.add_node("generate", _n_generate)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges(
        "grade", _route_after_grade, {"assemble": "assemble", "rewrite": "rewrite"}
    )
    g.add_edge("rewrite", "retrieve")
    g.add_edge("assemble", "generate")
    g.add_edge("generate", END)
    return g.compile()


_GRAPH = _build_graph()


def _run_simple(db, client, candidate_id, scope, spec, difficulty, focus, embed_fn, on_progress) -> list[Question]:
    """enable_loop=False：跳过评分/改写，直接 召回→拼上下文→分批生成。"""
    chunks = retrieve_by_scope(db, candidate_id, scope, k=KB_RECALL_K, embed_fn=embed_fn)
    if not chunks:
        return []
    picked = _trim_to_budget(_chunk_payloads(chunks), settings.doc_max_input_chars)
    seen: set[str] = set()
    created: list[Question] = []
    batches = build_batches(spec)
    total = sum(c for _, c in batches)
    done = 0
    for qtype, count in batches:
        payloads = _generate_batch(client, scope, qtype, count, difficulty, focus, picked, seen)
        created += _persist_questions(
            db, candidate_id, None, payloads or [], seen,
            source_chunk=picked[0]["content"] if picked else None,
        )
        done += count
        if on_progress:
            on_progress(min(done, total), total)
    return created


def generate_by_scope_graph(
    db,
    candidate_id: int,
    scope: str,
    spec: list[dict],
    difficulty: str = "medium",
    focus=None,
    enable_loop: bool = True,
    client=None,
    embed_fn=None,
    on_progress=None,
) -> list[Question]:
    """跨文档知识库出题主入口（路线③，LangGraph 编排）。返回生成的 Question 列表。"""
    from .llm_client import get_llm_client

    client = client or get_llm_client()
    spec = normalize_spec(spec)
    if not spec:
        return []

    # 空知识库：与路线②一致，直接返回，避免进入无意义的改写循环
    if not retrieve_by_scope(db, candidate_id, scope, k=KB_RECALL_K, embed_fn=embed_fn):
        return []

    if not enable_loop:
        return _run_simple(db, client, candidate_id, scope, spec, difficulty, focus, embed_fn, on_progress)

    state: KbState = {
        "db": db,
        "client": client,
        "candidate_id": candidate_id,
        "scope": scope,
        "k": KB_RECALL_K,
        "difficulty": difficulty,
        "focus": focus,
        "spec": spec,
        "embed_fn": embed_fn,
        "rewrites": 0,
        "seen": set(),
        "created": [],
        "done": 0,
        "on_progress": on_progress,
    }
    result = _GRAPH.invoke(state)
    return result.get("created") or []
