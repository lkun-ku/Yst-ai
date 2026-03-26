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
from ..utils import near_duplicate
from .validation import validate_question_payload

logger = logging.getLogger(__name__)

VALID_TYPES = ("single", "multiple", "judge", "blank", "short")

#: 路线①（按资料出题、章节配额）的近似判重阈值。
#: 与路线②③（跨文档自由出题，0.6）不同：章节配额模式下同一章节同一题型的题干
#: **天然高度相似**（模型会输出「下列关于 X 的表述，正确的是？」这类同模板变体），
#: 0.6 会把它们成批误杀——这正是此前「选 30 题只出几道」的直接原因。此处只拦
#: 几乎字面一致的重复（0.85）。
ROUTE1_DUP_THRESHOLD = 0.85


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
    limit: int | None = None,
    stems: list[str] | None = None,
    dup_threshold: float | None = None,
) -> list[Question]:
    """校验 + 去重后落库，返回新增题目。

    `stems`：跨批次累积的已落库题干（归一化后）。传入后启用**近似判重**（#26 遗留 3），
    拦住「字面微差、语义相同」的重复题；不传则只做精确去重（保持旧行为）。

    `dup_threshold`：覆盖配置的判重阈值。路线①（章节配额）同章节同题型题干天然相似，
    默认 0.6 会成批误杀，需传 0.85 只拦几乎字面一致的重复。

    `limit`：最多落库条数，在**校验与去重通过之后**生效（None 表示不限制，默认保持
    原有行为）。补偿轮会带余量生成以防去重损耗，靠它截断到真实缺口，避免超产（#23）。

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
        # 近似判重（#26 遗留 3）：精确去重拦不住「差一个空格/换种措辞」的语义重复题。
        # fake 模式跳过：它生成的是同模板伪题，彼此相似度约 0.8，判重会误杀（12 题只剩 3 题）。
        threshold = dup_threshold if dup_threshold is not None else settings.doc_stem_dup_threshold
        if (
            key
            and stems is not None
            and settings.llm_mode != "fake"
            and any(near_duplicate(key, s, threshold) for s in stems[-300:])
        ):
            logger.info("doc_generate: 与题干近似重复，丢弃：%s", (p.get("stem") or "")[:30])
            continue
        if key:
            seen.add(key)
            if stems is not None:
                stems.append(key)

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
        if limit is not None and len(created) >= limit:
            logger.info("doc_generate: 达到 limit=%d，停止落库（补偿轮避免超产）", limit)
            break
    return created


def generate_for_document(db, doc, chunks: list[dict], spec: list[dict], mode: str, difficulty: str, focus, scope,
                         on_progress=None, on_event=None) -> list[Question]:
    """执行生成（调用方需提供**独立 DB Session**，见 A6）。

    `on_event(type, text, detail)`：#26 首批新增，用于「按资料出题」页展示过程时间线。
    """
    client = get_llm_client()

    def emit(type_: str, text: str, detail=None) -> None:
        if on_event:
            on_event(type_, text, detail)
    spec = normalize_spec(spec)
    seen: set[str] = set()
    created: list[Question] = []
    all_stems: list[str] = []  # 跨批次累积的已落库题干，供近似判重（#26 遗留 3）

    def progress(done: int, total: int) -> None:
        if on_progress:
            on_progress(done, total)

    def fallback_generate(qtype: str, count: int, ctx_chunks: list[dict], gen_fn) -> list[dict]:
        """复用 #26 的质量闭环：规则校验 → 降粒度重试（不通过时 3→2→1）。

        说明：
        - 延迟导入 _generate_batch_with_fallback：kb_generate 依赖本模块的 _persist_questions
          等，顶层互导会形成循环依赖。
        - 关闭 LLM 自检：路线① 是章节配额、批数多（30 题约 10 批），逐批自检会额外增加
          大量 LLM 调用；路线① 原本就没有自检，这里不引入新开销。
        """
        from .kb_generate import _generate_batch_with_fallback

        return _generate_batch_with_fallback(
            client, scope, qtype, count, difficulty, focus, ctx_chunks, seen, ctx_chunks,
            gen_fn=gen_fn, selfcheck=False, emit=emit,
        )

    if mode == "spot":
        # 定点：Top-K 检索后按题型分批
        picked = retrieve(focus or "", chunks, k=settings.doc_top_k, scope=scope) or chunks
        picked = _trim_to_budget(picked, settings.doc_max_input_chars)
        batches = build_batches(spec)
        total = sum(c for _, c in batches)
        done = 0
        for qtype, count in batches:
            def gen_fn(size, mix):
                return client.generate(
                    GenerationRequest(
                        kind="doc_question",
                        knowledge_point=(focus or doc.title)[:128],
                        context={
                            "chunks": _chunk_payloads(picked),
                            "qtype": qtype,
                            "count": size,
                            "difficulty": difficulty,
                            "focus": focus,
                            "existing_stems": sorted(seen),
                        },
                    )
                ).payloads

            payloads = fallback_generate(qtype, count, picked, gen_fn)
            created += _persist_questions(
                db, doc.candidate_id, doc.id, payloads or [], seen,
                stems=all_stems, dup_threshold=ROUTE1_DUP_THRESHOLD,
            )
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

    # 取整损耗校正：每段每题型各自 round() 会让计划总数**小于**用户选择的题数
    # （实测 30 题 → grand_total 只有 25），而补偿轮以 grand_total 为目标，导致最终交付不足。
    # 这里把差额轮流均摊到各批次，保证计划总数 == 用户选择数，且分布尽量均匀。
    deficit = int(sum(i["count"] for i in spec)) - grand_total
    i = 0
    while deficit > 0 and batches:
        heading, qtype_, n = batches[i % len(batches)]
        batches[i % len(batches)] = (heading, qtype_, n + 1)
        deficit -= 1
        i += 1
    grand_total = sum(n for _, _, n in batches)

    def gen_batch(heading: str | None, qtype: str, count: int, pool_chunks: list[dict],
                  limit: int | None = None, dup_threshold: float = ROUTE1_DUP_THRESHOLD) -> None:
        def gen_fn(size, mix):
            picked = _trim_to_budget(pool_chunks, settings.doc_max_input_chars)
            return client.generate(
                GenerationRequest(
                    kind="doc_question",
                    knowledge_point=(heading or doc.title)[:128],
                    context={
                        "chunks": _chunk_payloads(picked),
                        "qtype": qtype,
                        "count": size,
                        "difficulty": difficulty,
                        "focus": focus,
                        "existing_stems": sorted(seen),
                    },
                )
            ).payloads

        payloads = fallback_generate(qtype, count, pool_chunks, gen_fn)
        created.extend(
            _persist_questions(
                db, doc.candidate_id, doc.id, payloads or [], seen,
                stems=all_stems, dup_threshold=dup_threshold, limit=limit,
            )
        )

    done = 0
    for heading, qtype, count in batches:
        pool = seg_chunks.get(heading or "", [])
        if not pool:
            continue
        gen_batch(heading, qtype, count, pool)
        done += count
        progress(min(done, grand_total), grand_total)

    # 补偿：因重复/校验被丢弃导致不足时，按 spec 顺序补足（最多 6 轮）。
    # 每轮**多生成候选**（need*2）：补偿阶段的题干与已落库题目重复率高，只生成 need 道大概率
    # 仍被判重丢弃；多生成后用 limit=need 截断落库——既不会超产，又显著提高命中率。
    attempts = 0
    while len(created) < grand_total and attempts < 6:
        attempts += 1
        for item in spec:
            if len(created) >= grand_total:
                break
            need = grand_total - len(created)
            want = min(settings.doc_batch_size * 2, max(need * 2, 2))
            gen_batch(None, item["type"], want, chunks, limit=need)
        progress(min(len(created), grand_total), grand_total)

    # 兜底：补偿后仍不足，说明资料内容已接近穷尽（小文档出大量题本就受限）。
    # 此时放宽判重到 0.95（只拦几乎完全相同的题干）再补一轮——
    # 宁可少数题干略有相近，也不让用户拿不到自己选择的题量。
    if len(created) < grand_total:
        need = grand_total - len(created)
        gen_batch(None, spec[0]["type"], max(need * 2, 4), chunks, limit=need, dup_threshold=0.95)

    return created
