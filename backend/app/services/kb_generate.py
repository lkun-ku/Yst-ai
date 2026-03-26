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
    picked_ids_of,
)
from .kb_events import slice_previews
from .kb_retrieval import retrieve_by_scope
from .validation import validate_question_payload
from .llm_client import get_llm_client, parse_doc_questions
from .prompts_kb import (
    bloom_distribution,
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


def compensation_count(need: int) -> int:
    """补偿轮生成题数：带余量、有上限（#23 出题欠产修复）。

    此前用 `min(doc_batch_size * 3, need)`，当 need=1 时只生成 1 道题——
    该题一旦因与已有题干重复被 seen 去重、或未通过结构化校验，本轮即完全空转，
    5 轮耗尽后仍永久欠产。故保证下限 doc_batch_size（缺口小也有候选），
    上限 doc_batch_size * 3（控制 token 成本）；超产由 _persist_questions(limit=) 截断。
    """
    return min(max(need * 2, settings.doc_batch_size), settings.doc_batch_size * 3)


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


def _ctx_for_question(p: dict, chunks: list[dict], limit: int) -> str:
    """自检依据：优先取该题**自己溯源到的切片**（#26）。

    此前把 `chunks[:6]` 拼接后再截 800 字，而生成侧可看 `doc_max_input_chars=30000`
    字——依据后段切片出的题在自检时看不到原文，会被必然判为「无依据」而误杀，
    这是大卷（8 题）0 产出的直接根因。
    """
    sid = p.get("source_id")
    if sid is not None:
        hit = next((c for c in chunks if str(c.get("id")) == str(sid)), None)
        if hit:
            return (hit.get("content") or "")[:limit]
    return "\n".join(c.get("content", "") for c in chunks[:6])[:limit]


def _ask_selfcheck(client, p: dict, chunks: list[dict], limit: int) -> bool | None:
    """单题自检。返回 True/False；None = LLM 不可用（调用方按「放行」处理）。"""
    ctx = _ctx_for_question(p, chunks, limit)
    text = client.ask(
        self_check_prompt(
            p.get("stem"), p.get("options"), p.get("answer"), p.get("explanation"), ctx
        )
    )
    if text is None:
        return None
    ok, _score, _issues = parse_selfcheck(text)
    return ok


def _selfcheck_batch(
    client,
    payloads: list[dict],
    chunks: list[dict],
    sample: int | None = None,
    ctx_limit: int | None = None,
) -> tuple[list[dict], bool]:
    """自检：LLM 不可用放行；支持抽检，抽检发现问题再升级为全批检（#26）。

    返回 (通过项, 是否全部通过)。sample 为 None 时全检（保持既有行为）。
    """
    limit = ctx_limit if ctx_limit is not None else settings.doc_selfcheck_chars
    targets = list(payloads or [])
    if not targets:
        return [], True

    checked = targets
    if sample and 0 < sample < len(targets):
        # 均匀取样（覆盖首尾），避免只检前几题
        step = len(targets) / sample
        idxs = sorted({min(len(targets) - 1, int(i * step)) for i in range(sample)})
        checked = [targets[i] for i in idxs]

    # 单趟遍历收集结果：避免在「发现不通过」后再对同一批重复调用一次（#26 P1）
    keep: list[dict] = []
    all_ok = True
    for p in checked:
        if _ask_selfcheck(client, p, chunks, limit) is False:
            all_ok = False
        else:
            keep.append(p)

    if all_ok:
        return list(targets), True
    if len(checked) < len(targets):
        # 抽检发现问题 → 升级为全批检（递归一次，全量遍历，不重复检测已检项）
        return _selfcheck_batch(client, targets, chunks, sample=None, ctx_limit=limit)
    return keep, False


def _generate_batch_with_fallback(
    client,
    scope,
    qtype,
    count,
    difficulty,
    focus,
    picked,
    seen,
    chunks,
    emit=None,
    sample=None,
    bloom: list[str] | None = None,
) -> list[dict]:
    """生成一批题：规则校验 → 自检 → 不通过则降粒度重试（如 6→3→2→1），#26。

    语义变更：不再 `payloads = regen or payloads`（自检不过即整批归零），
    而是**任何一轮都保留规则校验通过的题目**——自检用于标记风险，不应成为
    欠产的原因（与既有「LLM 不可用保守放行」的降级哲学一致）。
    """
    # 认知层级（#26 A）：未显式指定时用配置默认池（刻意不含 remember，避免整卷记忆题）
    levels = [s.strip() for s in (bloom or settings.doc_bloom_levels or "").split(",") if s.strip()]

    sizes: list[int] = []
    # 单次生成不超过 doc_batch_size：大批次结构化输出失败率显著上升（#26）
    n = min(int(count), settings.doc_batch_size)
    while n > 1:
        sizes.append(n)
        n = (n + 1) // 2  # 向上取整：粒度序列连续（3→2→1），不跳级
    sizes.append(1)
    sizes = list(dict.fromkeys(sizes))  # 去重保序

    accepted: list[dict] = []
    local_seen: set[str] = set()
    last_ok: list[dict] = []  # 最后一轮的「规则校验通过项」：自检全否时据此降级放行

    for size in sizes:
        if len(accepted) >= count:
            break
        payloads = _generate_batch(
            client, scope, qtype, size, difficulty, focus, picked, seen,
            bloom_mix=bloom_distribution(size, levels),
        )
        ok_payloads: list[dict] = []
        for p in payloads or []:
            if not isinstance(p, dict):
                continue
            if validate_question_payload(p, set()):  # 规则校验：第一道闸
                continue
            stem = str(p.get("stem") or "").strip()
            if not stem or stem in local_seen:
                continue
            local_seen.add(stem)
            ok_payloads.append(p)
        if not ok_payloads:
            if emit and size != sizes[0]:
                emit("stage", "第 %d 题粒度生成未通过规则校验，继续降粒度" % size)
            continue
        last_ok = ok_payloads

        passed, all_ok = _selfcheck_batch(client, ok_payloads, chunks, sample=sample)
        if emit:
            emit(
                "selfcheck",
                "自检 · %d/%d 通过%s"
                % (len(passed), len(ok_payloads), "（全部通过）" if all_ok else "（保留规则通过项）"),
            )
        accepted += passed

    # 兜底：自检若把所有题都否决（例如模型评判偏严），仍保留规则校验通过项。
    # 自检用于标记风险，不应成为欠产的原因——这正是本次修复的核心语义（#26）。
    if not accepted and last_ok:
        if emit:
            emit("selfcheck", "自检全部未通过，降级保留规则校验通过项（风险已记录）")
        return last_ok[:count]

    return accepted[:count]


def _generate_batch(client, scope, qtype, count, difficulty, focus, picked, seen, extra=None, bloom_mix=None):
    prompt = kb_question_prompt(
        picked, qtype, count, difficulty,
        ((focus or "") + ("；" + extra if extra else "")),
        sorted(seen), scope, bloom_mix=bloom_mix,
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
    on_event=None,
    bloom: list[str] | None = None,
) -> list[Question]:
    """跨文档知识库出题主入口（路线②）。返回生成的 Question 列表。

    `bloom`：布鲁姆认知层级池（如 ["understand","apply"]）；None 表示用配置默认值。

    `on_event(type, text, detail)`：#25 过程事件回调，供前端渲染时间线；
    取消信号由该回调抛出（见 routers/kb.py），故此处不吞异常。
    """
    client = client or get_llm_client()
    spec = normalize_spec(spec)
    if not spec:
        return []
    seen: set[str] = set()
    created: list[Question] = []
    all_stems: list[str] = []  # 跨批次累积的已落库题干，供近似判重（#26 遗留 3）

    def progress(done: int, total: int) -> None:
        if on_progress:
            on_progress(done, total)

    def emit(type_: str, text: str, detail=None) -> None:
        if on_event:
            on_event(type_, text, detail)

    # 1) 召回
    chunks = retrieve_by_scope(db, candidate_id, scope, k=KB_RECALL_K, embed_fn=embed_fn)
    if not chunks:
        return []
    emit("retrieve", f"已检索到 {len(chunks)} 个相关片段", slice_previews(chunks))

    # 2) 相关性评分 + 改写重检索（≤MAX_REWRITE）
    if enable_loop:
        graded, sufficient = _grade_and_filter(client, scope, chunks)
        rewrites = 0
        while not sufficient and rewrites < MAX_REWRITE:
            new_scope = _rewrite_scope(client, scope, "相关切片不足")
            if new_scope == scope:
                break
            emit("rewrite", f"查询改写：「{scope}」→「{new_scope}」")
            scope = new_scope
            chunks = retrieve_by_scope(db, candidate_id, scope, k=KB_RECALL_K, embed_fn=embed_fn)
            graded, sufficient = _grade_and_filter(client, scope, chunks)
            rewrites += 1
        emit("grade", f"相关性评分 · 通过 {len(graded or [])}/{len(chunks)} 片")
        if graded:
            chunks = graded

    # 3) 拼上下文（预算内）
    chunks = chunks[:KB_RECALL_K]
    picked = _trim_to_budget(_chunk_payloads(chunks), settings.doc_max_input_chars)

    # 4) 分批生成 + 自检重生成（≤MAX_REGEN 每批）
    batches = build_batches(spec)
    total = sum(c for _, c in batches)
    done = 0
    # 认知层级池（#26 A）：enable_loop=False 路径不走 fallback，需在此自行计算分布
    levels = [s.strip() for s in (bloom or settings.doc_bloom_levels or "").split(",") if s.strip()]
    for bi, (qtype, count) in enumerate(batches, 1):
        emit("batch", f"生成第 {bi}/{len(batches)} 批 · {qtype} × {count}")
        if enable_loop:
            # #26：一次生成 + 规则校验 + 自检 + 降粒度重试，失败也保留规则通过项
            payloads = _generate_batch_with_fallback(
                client, scope, qtype, count, difficulty, focus, picked, seen, chunks, emit=emit,
                sample=settings.doc_selfcheck_sample,  # P1：抽检（规则校验已前置为第一道闸）
                bloom=bloom,
            )
        else:
            payloads = _generate_batch(
                client, scope, qtype, count, difficulty, focus, picked, seen,
                bloom_mix=bloom_distribution(count, levels),
            )
        base = len(created)
        new_qs = _persist_questions(
            db, candidate_id, None, payloads or [], seen,
            source_chunk=build_source_chunk(picked),
            picked_ids=picked_ids_of(picked),
            stems=all_stems,
        )
        created += new_qs
        for i, q in enumerate(new_qs, 1):
            emit(
                "question",
                f"已出第 {base + i} 题",
                {"id": q.id, "stem": (q.stem or "")[:80], "type": str(getattr(q.type, "value", q.type))},
            )
        done += count
        progress(min(done, total), total)

    # 5) 补偿：不足时按 spec 顺序补足（最多 5 轮；带余量生成 + 按 need 截断落库，#23）
    attempts = 0
    while len(created) < total and attempts < 5:
        attempts += 1
        for qtype, count in batches:
            if len(created) >= total:
                break
            need = total - len(created)
            emit("stage", f"第 {attempts} 轮补偿 · 还差 {need} 题")
            if enable_loop:
                # 补偿同样走降粒度重试：大批量一次生成失败率高（#26）
                payloads = _generate_batch_with_fallback(
                    client, scope, qtype, need, difficulty, focus, picked, seen, chunks, emit=emit,
                    sample=settings.doc_selfcheck_sample, bloom=bloom,
                )
            else:
                payloads = _generate_batch(
                    client, scope, qtype, compensation_count(need),
                    difficulty, focus, picked, seen,
                    bloom_mix=bloom_distribution(compensation_count(need), levels),
                )
            created += _persist_questions(
                db, candidate_id, None, payloads or [], seen,
                source_chunk=build_source_chunk(picked),
                picked_ids=picked_ids_of(picked),
                limit=need, stems=all_stems,
            )
        progress(min(len(created), total), total)

    return created
