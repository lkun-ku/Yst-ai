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
from ..utils import parse_bloom_levels
from .doc_generate import (
    _persist_questions,
    _trim_to_budget,
    build_batches,
    build_source_chunk,
    normalize_spec,
    picked_ids_of,
)
from .kb_generate import (
    KB_RECALL_K,
    MAX_REWRITE,
    _chunk_payloads,
    _generate_batch,
    _generate_batch_with_fallback,
    _grade_and_filter,
    _rewrite_scope,
    _selfcheck_batch,
)
from .kb_events import slice_previews
from .kb_retrieval import retrieve_by_scope
from .prompts_kb import bloom_distribution

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
    on_event: object
    bloom: object


def _emit(s: KbState, type_: str, text: str, detail=None) -> None:
    """上报过程事件（#25）。取消信号由回调抛出以中断出题，故此处不吞异常。"""
    fn = s.get("on_event")
    if fn:
        fn(type_, text, detail)


# ---------------- 图节点（普通函数，返回状态增量） ----------------

def _n_retrieve(s: KbState) -> dict:
    chunks = retrieve_by_scope(
        s["db"], s["candidate_id"], s["scope"], k=s.get("k", KB_RECALL_K), embed_fn=s.get("embed_fn")
    )
    _emit(s, "retrieve", f"已检索到 {len(chunks)} 个相关片段", slice_previews(chunks))
    return {"chunks": chunks}


def _n_grade(s: KbState) -> dict:
    graded, sufficient = _grade_and_filter(s["client"], s["scope"], s["chunks"])
    _emit(s, "grade", f"相关性评分 · 通过 {len(graded or [])}/{len(s.get('chunks') or [])} 片")
    return {"graded": graded, "sufficient": sufficient}


def _route_after_grade(s: KbState) -> str:
    if s.get("sufficient") or s.get("rewrites", 0) >= MAX_REWRITE:
        return "assemble"
    return "rewrite"


def _n_rewrite(s: KbState) -> dict:
    new_scope = _rewrite_scope(s["client"], s["scope"], "相关切片不足")
    if new_scope == s["scope"]:
        return {"rewrites": MAX_REWRITE}  # 改写无效 → 强制终止循环，避免死循环
    _emit(s, "rewrite", f"查询改写：「{s['scope']}」→「{new_scope}」")
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
    all_stems: list[str] = []  # 跨批次累积的已落库题干，供近似判重（#26 遗留 3）
    batches = build_batches(spec)
    total = sum(c for _, c in batches)
    done = s.get("done", 0)

    for bi, (qtype, count) in enumerate(batches, 1):
        _emit(s, "batch", f"生成第 {bi}/{len(batches)} 批 · {qtype} × {count}")
        # 防批间同质化：层级轮转偏移 + 已覆盖考点（从已落库题收集）
        covered = sorted({str(q.knowledge_point) for q in created if q.knowledge_point})
        # #26：与路线②共享同一实现（规则校验 + 自检 + 降粒度重试，失败保留规则通过项）
        payloads = _generate_batch_with_fallback(
            client, scope, qtype, count, difficulty, focus, picked, seen, s["chunks"],
            emit=lambda t, x, d=None: _emit(s, t, x, d),
            sample=settings.doc_selfcheck_sample,  # P1：抽检（规则校验已前置）
            bloom=s.get("bloom"), bloom_offset=bi - 1, covered_kps=covered,
        )
        base = len(created)
        new_qs = _persist_questions(
            db, s["candidate_id"], None, payloads or [], seen,
            source_chunk=build_source_chunk(picked),
            picked_ids=picked_ids_of(picked),
            stems=all_stems,
        )
        created += new_qs
        for i, q in enumerate(new_qs, 1):
            _emit(
                s,
                "question",
                f"已出第 {base + i} 题",
                {"id": q.id, "stem": (q.stem or "")[:80], "type": str(getattr(q.type, "value", q.type))},
            )
        done += count
        if on_progress:
            on_progress(min(done, total), total)

    # 补偿：不足时按 spec 顺序补足（最多 5 轮；带余量生成 + 按 need 截断落库，#23）
    attempts = 0
    while len(created) < total and attempts < 5:
        attempts += 1
        for qtype, count in batches:
            if len(created) >= total:
                break
            need = total - len(created)
            _emit(s, "stage", f"第 {attempts} 轮补偿 · 还差 {need} 题")
            payloads = _generate_batch_with_fallback(
                client, scope, qtype, need, difficulty, focus, picked, seen, s["chunks"],
                emit=lambda t, x, d=None: _emit(s, t, x, d),
                sample=settings.doc_selfcheck_sample, bloom=s.get("bloom"), bloom_offset=attempts - 1,
                covered_kps=sorted({str(q.knowledge_point) for q in created if q.knowledge_point}),
            )
            created += _persist_questions(
                db, s["candidate_id"], None, payloads or [], seen,
                source_chunk=build_source_chunk(picked),
                picked_ids=picked_ids_of(picked),
                limit=need, stems=all_stems,
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


def _run_simple(db, client, candidate_id, scope, spec, difficulty, focus, embed_fn, on_progress,
                bloom: list[str] | None = None) -> list[Question]:
    """enable_loop=False：跳过评分/改写，直接 召回→拼上下文→分批生成。"""
    levels = bloom or parse_bloom_levels(settings.doc_bloom_levels)
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
        payloads = _generate_batch(
            client, scope, qtype, count, difficulty, focus, picked, seen,
            bloom_mix=bloom_distribution(count, levels),
        )
        created += _persist_questions(
            db, candidate_id, None, payloads or [], seen,
            source_chunk=build_source_chunk(picked),
            picked_ids=picked_ids_of(picked),
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
    on_event=None,
    bloom: list[str] | None = None,
) -> list[Question]:
    """跨文档知识库出题主入口（路线③，LangGraph 编排）。返回生成的 Question 列表。

    `bloom`：布鲁姆认知层级池（#26 A）；None 表示用配置默认值。

    `on_event(type, text, detail)`：#25 过程事件回调；取消信号由该回调抛出以中断出题。
    """
    from .llm_client import get_llm_client

    client = client or get_llm_client()
    spec = normalize_spec(spec)
    if not spec:
        return []

    # 空知识库：与路线②一致，直接返回，避免进入无意义的改写循环
    if not retrieve_by_scope(db, candidate_id, scope, k=KB_RECALL_K, embed_fn=embed_fn):
        return []

    if not enable_loop:
        return _run_simple(db, client, candidate_id, scope, spec, difficulty, focus, embed_fn,
                           on_progress, bloom=bloom)

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
        "on_event": on_event,
        "bloom": bloom,
    }
    result = _GRAPH.invoke(state)
    return result.get("created") or []
