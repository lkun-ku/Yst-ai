"""路线②：手写质量闭环知识库出题（跨文档范围出题）。

与路线③（LangGraph 编排，kb_graph.py）共享同一套检索 / 提示词 / LLM·Embedding 接缝 /
校验落库逻辑；**唯一差异是编排层**（此处为显式 Python 控制流，路线③为 StateGraph）。
这样实验测量的正是「框架 vs 手写编排」，排除无关变量（见方案文档对照实验设计）。

质量闭环（任一 LLM 调用返回 None 即降级，不阻塞出题）：
1. 跨文档召回：retrieve_by_scope（RRF 融合 + 三级降级）；
2. 检索相关性评分：对召回切片打分过滤；相关不足则**查询改写重检索（≤1 次）**；
3. 拼上下文（预算内，复用 _trim_to_budget）；
4. 分批生成 + **生成自检**（不合格批重生成 ≤1 次）；
5. validate_question_payload 校验落库（Module.PERSONAL，owner_candidate_id 隔离）。
"""

from __future__ import annotations

import logging

from ..config import settings
from ..models import Question
from .doc_generate import (
    _persist_questions,
    _trim_to_budget,
    build_batches,
    build_source_chunk,
    normalize_spec,
)
from .kb_retrieval import retrieve_by_scope
from .llm_client import get_llm_client, parse_doc_questions
from .prompts_kb import (
    kb_question_prompt,
    parse_relevance,
    parse_selfcheck,
    relevance_grade_prompt,
    scope_rewrite_prompt,
    self_check_prompt,
)

logger = logging.getLogger(__name__)

KB_RECALL_K = 16          # 召回切片数（大于最终拼上下文所需，供评分过滤）
RELEVANCE_THRESHOLD = 0.5
MAX_REWRITE = 1           # 查询改写重检索上限（控制成本 ~1.5~2x）
MAX_REGEN = 1             # 单批生成自检失败后的重生成上限


# ---------------- 质量闭环子步骤 ----------------

def _chunk_payloads(chunks: list[dict]) -> list[dict]:
    """转为喂给模型的切片 payload。携带 `id` 供工单 17 的批级溯源使用
    （`kb_question_prompt` 只读 seq/heading_path/content，多出的键不影响提示词）。
    """
    return [
        {
            "id": c.get("id"),
            "seq": c.get("seq"),
            "heading_path": c.get("heading_path"),
            "content": c.get("content"),
        }
        for c in chunks
    ]


def _grade_and_filter(client, scope: str, chunks: list[dict]) -> tuple[list[dict], bool]:
    """对召回切片做相关性评分并过滤；LLM 不可用时保守返回全部（退化）。"""
    kept: list[dict] = []
    for c in chunks:
        text = client.ask(relevance_grade_prompt(scope, c.get("content", "")))
        if text is None:
            return chunks, True  # 降级：保留全部
        relevant, score = parse_relevance(text)
        nc = dict(c)
        nc["relevance"] = score
        if relevant and score >= RELEVANCE_THRESHOLD:
            kept.append(nc)
    return kept, len(kept) >= max(1, len(chunks) // 2)


def _rewrite_scope(client, scope: str, reason: str = "") -> str:
    text = client.ask(scope_rewrite_prompt(scope, reason))
    if not text:
        return scope
    return text.strip() or scope


def _selfcheck_batch(client, payloads: list[dict], chunks: list[dict]) -> tuple[list[dict], bool]:
    """逐题自检；LLM 不可用则全部放行（退化）。返回 (通过项, 是否全部通过)。"""
    ctx = "\n".join(c.get("content", "") for c in chunks[:6])
    passed_list: list[dict] = []
    all_ok = True
    for p in payloads or []:
        text = client.ask(
            self_check_prompt(
                p.get("stem"), p.get("options"), p.get("answer"), p.get("explanation"), ctx
            )
        )
        if text is None:
            passed_list.append(p)
            continue
        ok, _score, _issues = parse_selfcheck(text)
        if ok:
            passed_list.append(p)
        else:
            all_ok = False
    return passed_list, all_ok


def _generate_batch(client, scope, qtype, count, difficulty, focus, picked, seen, extra=None):
    prompt = kb_question_prompt(
        picked, qtype, count, difficulty,
        ((focus or "") + ("；" + extra if extra else "")),
        sorted(seen), scope,
    )
    text = client.ask(prompt)
    if text is None:
        return []
    return parse_doc_questions(text)


# ---------------- 主入口 ----------------

def generate_by_scope(
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
    """跨文档知识库出题主入口（路线②）。返回生成的 Question 列表。"""
    client = client or get_llm_client()
    spec = normalize_spec(spec)
    if not spec:
        return []
    seen: set[str] = set()
    created: list[Question] = []

    def progress(done: int, total: int) -> None:
        if on_progress:
            on_progress(done, total)

    # 1) 召回
    chunks = retrieve_by_scope(db, candidate_id, scope, k=KB_RECALL_K, embed_fn=embed_fn)
    if not chunks:
        return []

    # 2) 相关性评分 + 改写重检索（≤MAX_REWRITE）
    if enable_loop:
        graded, sufficient = _grade_and_filter(client, scope, chunks)
        rewrites = 0
        while not sufficient and rewrites < MAX_REWRITE:
            new_scope = _rewrite_scope(client, scope, "相关切片不足")
            if new_scope == scope:
                break
            scope = new_scope
            chunks = retrieve_by_scope(db, candidate_id, scope, k=KB_RECALL_K, embed_fn=embed_fn)
            graded, sufficient = _grade_and_filter(client, scope, chunks)
            rewrites += 1
        if graded:
            chunks = graded

    # 3) 拼上下文（预算内）
    chunks = chunks[:KB_RECALL_K]
    picked = _trim_to_budget(_chunk_payloads(chunks), settings.doc_max_input_chars)

    # 4) 分批生成 + 自检重生成（≤MAX_REGEN 每批）
    batches = build_batches(spec)
    total = sum(c for _, c in batches)
    done = 0
    for qtype, count in batches:
        payloads = _generate_batch(client, scope, qtype, count, difficulty, focus, picked, seen)
        if enable_loop:
            payloads, ok = _selfcheck_batch(client, payloads, chunks)
            if not ok and MAX_REGEN > 0:
                regen = _generate_batch(
                    client, scope, qtype, count, difficulty, focus, picked, seen,
                    extra="请更严格忠于资料、避免幻觉与超纲",
                )
                regen, _ = _selfcheck_batch(client, regen, chunks)
                payloads = regen or payloads
        created += _persist_questions(
            db, candidate_id, None, payloads or [], seen,
            source_chunk=build_source_chunk(picked),
        )
        done += count
        progress(min(done, total), total)

    # 5) 补偿：不足时按 spec 顺序补足（最多 3 轮，沿用 doc_generate 经验）
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
                db, candidate_id, None, payloads or [], seen,
                source_chunk=build_source_chunk(picked),
            )
        progress(min(len(created), total), total)

    return created
