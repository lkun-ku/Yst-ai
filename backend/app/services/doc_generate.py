"""按题型 / 章节配额分批生成题目（AI 出题管线核心）。

两种模式（决策三）：
- `paper` 整卷模式：**章节配额分配**，保证覆盖均匀，避免题目扎堆
- `spot` 定点模式：Top-K 语义检索，精准命中指定知识点

生成策略（决策四）：**分批生成**，每批 5~10 题。
单次生成整卷输出约 10~15K tokens，截断与格式崩坏概率高且无部分成功；
分批后每批约 2K tokens，JSON 可靠性高一个数量级，坏批丢弃不影响其他批。
"""

from __future__ import annotations

import json
import logging

from ..config import settings
from ..models import Module, Question, QuestionSource, QuestionType
from .doc_parser import normalize_stem
from .embedding import retrieve
from .llm_client import GenerationRequest, get_llm_client
from .validation import validate_question_payload

logger = logging.getLogger(__name__)

VALID_TYPES = ("single", "multiple", "judge", "blank", "short")


# ---------------- 纯逻辑：配比归一化 / 分批 / 配额分配 ----------------

def normalize_spec(spec: list[dict] | None, max_total: int | None = None) -> list[dict]:
    """过滤非法题型与非正数，并按需截断总题量（成本硬约束）。"""
    limit = max_total if max_total is not None else settings.doc_max_q_per_task
    items: list[dict] = []
    for it in spec or []:
        t = str(it.get("type") or "").strip()
        try:
            c = int(it.get("count") or 0)
        except (TypeError, ValueError):
            c = 0
        if t in VALID_TYPES and c > 0:
            items.append({"type": t, "count": c})

    total = sum(i["count"] for i in items)
    if limit and total > limit:
        excess = total - limit
        for i in range(len(items) - 1, -1, -1):
            if excess <= 0:
                break
            take = min(excess, items[i]["count"])
            items[i]["count"] -= take
            excess -= take
        items = [i for i in items if i["count"] > 0]
    return items


def build_batches(spec: list[dict], batch_size: int | None = None) -> list[tuple[str, int]]:
    """把配比切成批次：`[("single", 6), ("single", 4)]`。"""
    size = batch_size or settings.doc_batch_size
    out: list[tuple[str, int]] = []
    for item in spec or []:
        qtype = item["type"]
        remaining = int(item["count"])
        while remaining > 0:
            take = min(size, remaining)
            out.append((qtype, take))
            remaining -= take
    return out


def allocate_quota(segments: list[dict], total: int) -> list[int]:
    """按段长比例分配题量（最大余数法），保证 `sum(结果) == total`。"""
    n = len(segments)
    if n == 0 or total <= 0:
        return [0] * n

    weights = [max(0, int(s.get("char_count") or 0)) for s in segments]
    wsum = sum(weights)

    if wsum == 0:
        base, rem = divmod(total, n)
        out = [base] * n
        for i in range(rem):
            out[i] += 1
        return out

    exact = [total * w / wsum for w in weights]
    out = [int(x) for x in exact]
    rem = total - sum(out)
    # 按小数部分降序补余数；不足时至少保证前几段有题
    order = sorted(range(n), key=lambda i: -(exact[i] - out[i]))
    i = 0
    while rem > 0:
        out[order[i % n]] += 1
        rem -= 1
        i += 1
    return out


def build_segments(chunks: list[dict]) -> list[dict]:
    """按 heading_path 聚合成段；无标题结构的文档整篇作为一段。"""
    segs: dict[str, dict] = {}
    order: list[str] = []
    for c in chunks:
        key = c.get("heading_path") or ""
        if key not in segs:
            segs[key] = {"heading_path": key or None, "chunk_count": 0, "char_count": 0}
            order.append(key)
        segs[key]["chunk_count"] += 1
        segs[key]["char_count"] += int(c.get("char_count") or len(c.get("content") or ""))
    return [segs[k] for k in order]


# ---------------- 生成 ----------------

def _chunk_payloads(chunks: list[dict]) -> list[dict]:
    """转为喂给模型的切片 payload。

    携带 `id` 供工单 17 的批级溯源使用（`kb_question_prompt` 只读
    seq/heading_path/content，多出的键不影响提示词）。
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


def build_source_chunk(picked: list[dict] | None) -> str | None:
    """构造 `Question.source_chunk` 溯源值：该批**全部**召回切片的 id 清单 JSON。

    工单 17：原实现为 `picked[0]["content"]`，同批所有题共享首个切片内容，
    导致溯源可能指向错误切片（题目实际源自 picked[3]，却指向 picked[0]）。
    改为记录整批切片 id（全局唯一，可回查 `document_chunks`），消除误导。

    `picked` 为空或无有效 id 时返回 None，行为与原先一致。
    """
    if not picked:
        return None
    ids = [c.get("id") for c in picked if c.get("id") is not None]
    return json.dumps(ids, ensure_ascii=False) if ids else None


def picked_ids_of(picked: list[dict] | None) -> set[int]:
    """本次召回切片的 id 集合。

    工单 20/W-6：用于校验模型回传的 `source_id` 是否真实存在于本次召回切片中，
    防止模型幻觉出不存在的编号导致溯源指向错误。
    """
    if not picked:
        return set()
    return {c["id"] for c in picked if c.get("id") is not None}


def _trim_to_budget(chunks: list[dict], max_chars: int) -> list[dict]:
    """超出上下文预算时**均匀抽样**而非截断，保证覆盖而非只取开头。"""
    total = sum(len(c.get("content") or "") for c in chunks)
    if total <= max_chars or not chunks:
        return chunks
    keep = max(1, int(len(chunks) * max_chars / total))
    step = len(chunks) / keep
    return [chunks[int(i * step)] for i in range(keep)]


def _persist_questions(
    db,
    candidate_id: int,
    doc_id: int,
    payloads: list[dict],
    seen: set[str],
    source_chunk: str | None = None,
    picked_ids: set[int] | None = None,
) -> list[Question]:
    """校验 + 去重后落库，返回新增题目。

    溯源（工单 17 批级 + 工单 20/W-6 逐题）：
    - **优先**用模型回传的 `source_id`（提示词要求每题输出所依据切片编号，逐题精确）；
    - 该 id 必须落在本次召回切片集合 `picked_ids` 内，防模型幻觉出不存在的编号；
    - 缺失或非法则**回退**到批级 `source_chunk`（id 清单 JSON）。

    两种精度统一存「切片 id 清单 JSON」（`"[12]"` 或 `"[12,35]"`），前端展示逻辑一致。
    """
    created: list[Question] = []
    for p in payloads:
        if not isinstance(p, dict):
            continue
        errors = validate_question_payload(p, set())
        if errors:
            logger.info("doc_generate: 校验未通过，丢弃：%s", errors)
            continue
        key = normalize_stem(p.get("stem"))
        if key and key in seen:
            logger.info("doc_generate: 重复题干，丢弃：%s", (p.get("stem") or "")[:30])
            continue
        if key:
            seen.add(key)

        # 逐题溯源：优先模型回传的 source_id（校验在召回集合内），否则批级兜底
        sc = source_chunk
        sid = p.get("source_id")
        if sid is not None:
            try:
                sid_int = int(sid)
            except (TypeError, ValueError):
                sid_int = None
            if sid_int is not None and (picked_ids is None or sid_int in picked_ids):
                sc = json.dumps([sid_int], ensure_ascii=False)

        q = Question(
            module=Module.PERSONAL,
            knowledge_point=str(p.get("knowledge_point") or "")[:128],
            stem=str(p.get("stem") or ""),
            options=json.dumps(p.get("options") or [], ensure_ascii=False),
            answer=json.dumps(p.get("answer") or [], ensure_ascii=False),
            explanation=str(p.get("explanation") or ""),
            type=QuestionType(str(p.get("type") or "single")),
            source=QuestionSource.DOC,
            owner_candidate_id=candidate_id,
            doc_id=doc_id,
            source_chunk=sc,
        )
        db.add(q)
        created.append(q)
    return created


def generate_for_document(db, doc, chunks: list[dict], spec: list[dict], mode: str, difficulty: str, focus, scope, on_progress=None) -> list[Question]:
    """执行生成（调用方需提供**独立 DB Session**，见 A6）。"""
    client = get_llm_client()
    spec = normalize_spec(spec)
    seen: set[str] = set()
    created: list[Question] = []

    def progress(done: int, total: int) -> None:
        if on_progress:
            on_progress(done, total)

    if mode == "spot":
        # 定点：Top-K 检索后按题型分批
        picked = retrieve(focus or "", chunks, k=settings.doc_top_k, scope=scope) or chunks
        picked = _trim_to_budget(picked, settings.doc_max_input_chars)
        batches = build_batches(spec)
        total = sum(c for _, c in batches)
        done = 0
        for qtype, count in batches:
            payloads = client.generate(
                GenerationRequest(
                    kind="doc_question",
                    knowledge_point=(focus or doc.title)[:128],
                    context={
                        "chunks": _chunk_payloads(picked),
                        "qtype": qtype,
                        "count": count,
                        "difficulty": difficulty,
                        "focus": focus,
                        "existing_stems": sorted(seen),
                    },
                )
            ).payloads
            created += _persist_questions(db, doc.candidate_id, doc.id, payloads or [], seen)
            done += count
            progress(done, total)
        return created

    # 整卷：章节配额分配
    segments = build_segments(chunks)
    if scope:
        wanted = set(scope)
        segments = [s for s in segments if s["heading_path"] in wanted] or segments

    seg_chunks: dict[str, list[dict]] = {}
    for c in chunks:
        seg_chunks.setdefault(c.get("heading_path") or "", []).append(c)

    total_q = sum(i["count"] for i in spec)
    quotas = allocate_quota(segments, total_q)

    # 段内按**题型**比例分配（用 spec 而非 build_batches）。
    # 关键：不能用 build_batches(spec) —— 它已把题数切成多个批次，再按批次分配会让
    # 同一段同一题型被拆成多个批次，各批 LLM 都从"变式1"编号 → 大量重复被去重丢弃，
    # 实际出题数少于用户选择数。
    batches: list[tuple[str | None, str, int]] = []
    for seg, quota in zip(segments, quotas):
        if quota <= 0:
            continue
        for item in spec:
            n = round(quota * item["count"] / max(1, total_q))
            remaining = int(n)
            while remaining > 0:
                take = min(settings.doc_batch_size, remaining)
                batches.append((seg["heading_path"], item["type"], take))
                remaining -= take

    grand_total = sum(n for _, _, n in batches)

    def gen_batch(heading: str | None, qtype: str, count: int, pool_chunks: list[dict]) -> None:
        picked = _trim_to_budget(pool_chunks, settings.doc_max_input_chars)
        payloads = client.generate(
            GenerationRequest(
                kind="doc_question",
                knowledge_point=(heading or doc.title)[:128],
                context={
                    "chunks": _chunk_payloads(picked),
                    "qtype": qtype,
                    "count": count,
                    "difficulty": difficulty,
                    "focus": focus,
                    "existing_stems": sorted(seen),
                },
            )
        ).payloads
        created.extend(_persist_questions(db, doc.candidate_id, doc.id, payloads or [], seen))

    done = 0
    for heading, qtype, count in batches:
        pool = seg_chunks.get(heading or "", [])
        if not pool:
            continue
        gen_batch(heading, qtype, count, pool)
        done += count
        progress(min(done, grand_total), grand_total)

    # 补偿：因重复/校验被丢弃导致不足时，按 spec 顺序补足（最多 3 轮，避免死循环）
    attempts = 0
    while len(created) < grand_total and attempts < 3:
        attempts += 1
        for item in spec:
            if len(created) >= grand_total:
                break
            need = grand_total - len(created)
            gen_batch(None, item["type"], min(settings.doc_batch_size, need), chunks)
        progress(min(len(created), grand_total), grand_total)

    return created
