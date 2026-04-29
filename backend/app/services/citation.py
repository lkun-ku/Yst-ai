"""引用校验（`source_quote`）：把「不许编造」从提示词约束变成**可布尔判定**的硬闸门。

**为什么是子串匹配，而不是语义相似度或 LLM 判断**

- 语义相似度有**假阳性**：把「《教师法》第七条」写成「第八条」，在向量空间里仍与原文
  高度接近（同一部法、同一类措辞），相似度能到 0.9+，判不出来 ——
  而**编号级篡改恰好是本产品最致命的错误**（"句句有出处"的主张就是死在编号上）。
- LLM 判断有**不确定性**：同一输入两次可能给不同结论，无法作为"零编造"的证据；
  且它自己也会幻觉 —— 用可能幻觉的组件去校验幻觉，只是把风险挪了个位置。
- 子串匹配的失败模式是**已知且单向**的：它只会「误杀真实引用」（假阴性），
  不会「放过编造」—— 前提是不做任何语义等价扩展。法条场景里，
  漏放一条编造远比误杀十条真实引用严重，这个方向是正确的。

**代价（必须说清）**：子串匹配确实会误杀。模型引用时改了标点、断行，
或用了「《教师法》第七条：原文」这种带出处的写法，原样比对必然失败。
所以这里做**分层判定并分别计数**：

| 层级 | 判定 | 说明 |
| --- | --- | --- |
| `exact` | 原样子串 | 证据最强 |
| `normalized` | **只比较汉字/字母/数字**后子串 | 去空白、去标点、剥书名号前缀 |
| `fabricated` | 都不命中 | **编造** —— 总是拦截 |
| `too_short` | 归一化后长度不足 | **引用过弱、不足以作为证据** —— 也是策略性拦截，但**不叫编造** |
| `missing` | 没给引用 | 是否拦截由调用方决定（提示词遵守度问题，不是编造） |

**`too_short` 与 `fabricated` 必须分开**：`第七条` 这种三字引用在原文里**逐字存在**，
按真值定义它是真实引用、不是编造 —— 把它记成编造会让"零编造"这个指标变得不诚实。
它被拦下的理由是另一条（过短不能作为证据），所以有独立的状态与计数。

**两层的差距（exact 占比）本身就是可观察指标**，而不是被抹平的中间态 ——
它衡量「模型改写原文」的程度。若归一化层承担了全部命中，说明模型几乎不逐字引用，
提示词需要收紧。

**为什么剥书名号、但不剥条号**

模型被要求"给出处"时最自然的写法是「《教师法》第七条：原文」，其中 `《教师法》`
是**元数据**（切片正文里没有，只在 `heading_path` 里），不剥会把一批真实引用判为编造。
但**条号必须参与比对**：若剥掉 `第X条`，那么「《教师法》第九十九条 + 第七条的正文」
这种错配就会通过校验 —— 而这正是引用校验最该抓的一类错误。
（`kb_corpus` 的切片正文本身就以「第X条」开头，所以条号留在比对串里是自然的。）

**短路保护（`too_short`）**：归一化后长度低于 `citation_min_quote_chars` 的引用被拦下，
理由是它**不能作为证据** ——「第七条」这种三字引用在任何法条库里都能命中，
放过它等于把闸门做成摆设（模型只要抄个条号就能满足校验）。

它**不叫编造**：三字引用在原文里逐字存在，按真值定义是真引用。
把政策性拦截混进"零编造"指标会让那个数字变得不诚实，所以它有独立状态与计数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

#: payload 中承载引用的字段名（单条）。多引用用 `source_quotes`。
QUOTE_FIELD = "source_quote"
QUOTE_LIST_FIELD = "source_quotes"

#: 判定层级。
STATUS_EXACT = "exact"          # 原样子串命中
STATUS_NORMALIZED = "normalized"  # 仅去空白/标点/书名号后命中
STATUS_FABRICATED = "fabricated"  # 提供了引用但无法定位 → 编造（拦截）
STATUS_TOO_SHORT = "too_short"    # 引用过弱、不足以作为证据（拦截，但不是编造）
STATUS_MISSING = "missing"        # 未提供引用（不是编造，但也没有证据）

#: 视为"有引用且有效"的状态。
OK_STATUSES = (STATUS_EXACT, STATUS_NORMALIZED)
#: 默认最短引用长度（归一化后，按可读字符计）。
DEFAULT_MIN_QUOTE_CHARS = 6

#: 书名号前缀：`《中华人民共和国教师法》` —— 只在**开头**出现一次时剥离。
#: 不剥离会把最自然的引用写法误判为编造；而它确实是元数据（正文里没有）。
_LEADING_BOOK_RE = re.compile(r"^《[^》]{1,40}》\s*")

#: 全角 → 半角（数字与字母）。模型混用全角数字是常见现象，且不构成内容差异。
_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)

#: 归一化时保留的字符：汉字 / 字母 / 数字。其余（空白、标点、书名号、引号）一律丢弃 ——
#: 丢弃标点会放过"只改标点"的引用差异，这是刻意的：标点不是内容，
#: 而保留它反而会因《》，。：等全半角混用而误杀真实引用。
_KEEP_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")


def normalize_for_match(text: str) -> str:
    """把引用/正文归一化成「只含可读字符」的比对串。

    步骤：剥开头书名号 → 全角转半角 → 只保留汉字/字母/数字 → 小写。
    """
    t = _LEADING_BOOK_RE.sub("", str(text or ""))
    t = t.translate(_FULLWIDTH)
    return "".join(_KEEP_RE.findall(t)).lower()


def _content_of(chunk) -> str:
    """切片可以是 dict（`kb_retrieval` 返回的结构）或裸字符串。"""
    if isinstance(chunk, dict):
        return str(chunk.get("content") or "")
    return str(chunk or "")


def _id_of(chunk) -> int | None:
    if isinstance(chunk, dict):
        cid = chunk.get("id")
        return int(cid) if isinstance(cid, (int, float, str)) and str(cid).isdigit() else None
    return None


@dataclass(frozen=True)
class QuoteCheck:
    """一条引用的校验结果。"""

    quote: str
    status: str
    chunk_id: int | None = None  # 命中的首个切片 id（未命中为 None）

    @property
    def ok(self) -> bool:
        """是否可定位（exact / normalized）。"""
        return self.status in OK_STATUSES

    @property
    def fabricated(self) -> bool:
        """是否提供了引用但无法定位（真编造）。"""
        return self.status == STATUS_FABRICATED

    @property
    def too_short(self) -> bool:
        """引用过弱、不足以作为证据（内容可能是真的，但强度不够）。"""
        return self.status == STATUS_TOO_SHORT

    def as_dict(self) -> dict:
        return {"quote": self.quote, "status": self.status, "chunk_id": self.chunk_id}


def verify_quote(
    quote: str,
    chunks: Sequence,
    min_chars: int = DEFAULT_MIN_QUOTE_CHARS,
) -> QuoteCheck:
    """校验一条引用能否在给定切片中定位。

    先原样比对（`exact`），再归一化比对（`normalized`），都不中即 `fabricated`。
    长度不足 `min_chars` 的在前置检查里判为 `too_short`。
    """
    raw = str(quote or "").strip()
    if not raw:
        return QuoteCheck("", STATUS_MISSING, None)

    # ⓿ 过短：不足以作为证据 → 独立状态。
    # 放在最前，**先于** exact —— 否则"第七条"会因逐字存在而走 exact 放行，
    # 于是一条三字引用就能满足闸门（这正是本检查要挡的）。
    if len(normalize_for_match(raw)) < max(1, int(min_chars)):
        return QuoteCheck(raw, STATUS_TOO_SHORT, None)

    # ① 原样子串：证据最强，且不需要任何容错
    for c in chunks or []:
        if raw in _content_of(c):
            return QuoteCheck(raw, STATUS_EXACT, _id_of(c))

    # ② 归一化子串：容忍标点/空白/全半角/书名号的写法差异
    nq = normalize_for_match(raw)
    for c in chunks or []:
        if nq in normalize_for_match(_content_of(c)):
            return QuoteCheck(raw, STATUS_NORMALIZED, _id_of(c))

    return QuoteCheck(raw, STATUS_FABRICATED, None)


def quotes_of(payload: dict) -> list[str]:
    """从一个 payload 中取出它声明的所有引用。

    支持 `source_quote`（字符串）与 `source_quotes`（列表）两种写法 ——
    模型两种都写过，只认一种会静默产生一批「无引用」的假数据。
    """
    if not isinstance(payload, dict):
        return []
    out: list[str] = []
    single = payload.get(QUOTE_FIELD)
    if isinstance(single, str):
        out.append(single)
    elif isinstance(single, (list, tuple)):
        out.extend(str(x) for x in single)
    many = payload.get(QUOTE_LIST_FIELD)
    if isinstance(many, (list, tuple)):
        out.extend(str(x) for x in many)
    elif isinstance(many, str):
        out.append(many)
    return [q.strip() for q in out if str(q).strip()]


@dataclass(frozen=True)
class CitationReport:
    """一批 payload 的引用校验汇总（**生产侧**：只有计数，没有"真值"）。"""

    per_payload: tuple[tuple[QuoteCheck, ...], ...] = ()
    chunk_count: int = 0

    @property
    def n_payloads(self) -> int:
        return len(self.per_payload)

    @property
    def checks(self) -> tuple[QuoteCheck, ...]:
        return tuple(c for group in self.per_payload for c in group)

    def _count(self, status: str) -> int:
        return sum(1 for c in self.checks if c.status == status)

    @property
    def n_exact(self) -> int:
        return self._count(STATUS_EXACT)

    @property
    def n_normalized(self) -> int:
        return self._count(STATUS_NORMALIZED)

    @property
    def n_fabricated(self) -> int:
        return self._count(STATUS_FABRICATED)

    @property
    def n_too_short(self) -> int:
        return self._count(STATUS_TOO_SHORT)

    @property
    def n_missing(self) -> int:
        return self._count(STATUS_MISSING)

    @property
    def hit_rate(self) -> float:
        """**给出了引用**的条目中可定位的比例。

        分母含 `fabricated` 与 `too_short`（两者都算"给了引用但不可用"），
        不含 `missing` —— 否则"漏字段"会稀释真正有效的引用质量。
        """
        given = self.n_exact + self.n_normalized + self.n_fabricated + self.n_too_short
        if not given:
            return 0.0
        return round((self.n_exact + self.n_normalized) / given, 4)

    @property
    def exact_ratio(self) -> float:
        """可定位的引用里，有多少是**逐字一致**的。

        这是「模型改写原文」程度的直接度量：若该值低，说明提示词需要收紧。
        """
        ok = self.n_exact + self.n_normalized
        return round(self.n_exact / ok, 4) if ok else 0.0

    @property
    def quote_coverage(self) -> float:
        """**给了引用**的 payload 占比（衡量"是否在引用"，与准不准无关）。"""
        if not self.n_payloads:
            return 0.0
        gave = sum(1 for g in self.per_payload if any(c.status != STATUS_MISSING for c in g))
        return round(gave / self.n_payloads, 4)

    def blockable(self, require_quote: bool = False) -> tuple[int, ...]:
        """应被拦截的 payload 下标。

        - `fabricated`（编造）→ **总是**拦截：这是产品的红线，不接受配置。
        - `too_short`（引用过弱）→ **总是**拦截：它不能作为证据，放行等于闸门形同虚设。
        - `missing`（没给引用）→ 仅当 `require_quote=True` 时拦截 ——
          这是提示词遵守度问题，不是编造，默认不该因此欠产。
        """
        out: list[int] = []
        for i, group in enumerate(self.per_payload):
            if any(c.fabricated or c.too_short for c in group):
                out.append(i)
            elif require_quote and all(c.status == STATUS_MISSING for c in group):
                out.append(i)
        return tuple(out)

    def as_row(self) -> dict:
        """拍平成一行，便于写对比表。"""
        return {
            "payloads": self.n_payloads,
            "quotes": len(self.checks),
            "exact": self.n_exact,
            "normalized": self.n_normalized,
            "fabricated": self.n_fabricated,
            "too_short": self.n_too_short,
            "missing": self.n_missing,
            "hit_rate": self.hit_rate,
            "exact_ratio": self.exact_ratio,
            "coverage": self.quote_coverage,
        }


def verify_payloads(
    payloads: Sequence[dict],
    chunks: Sequence,
    min_chars: int = DEFAULT_MIN_QUOTE_CHARS,
) -> CitationReport:
    """对一批 payload 逐条校验其声明的引用。

    **没给引用的 payload 也会产生一条 `missing` 检查**，而不是留空组 ——
    否则"未给引用的题数"在报告里根本不可见（只能靠 payload 与检查项的数量差去反推），
    而它恰恰是最该被盯住的信号：模型开始漏字段时，命中率会好看得毫无变化。
    """
    groups: list[tuple[QuoteCheck, ...]] = []
    for p in payloads or []:
        quotes = quotes_of(p)
        if not quotes:
            groups.append((QuoteCheck("", STATUS_MISSING, None),))
            continue
        groups.append(tuple(verify_quote(q, chunks, min_chars) for q in quotes))
    return CitationReport(per_payload=tuple(groups), chunk_count=len(chunks or []))


def split_by_citation(
    payloads: Sequence[dict],
    chunks: Sequence,
    require_quote: bool = False,
    min_chars: int = DEFAULT_MIN_QUOTE_CHARS,
) -> tuple[list[dict], list[dict], CitationReport]:
    """**闸门**：把 payload 分成 (通过, 拦截) 两批，并返回校验报告。

    拦截条件是「含无法定位的引用」—— 这是布尔判定，不涉及任何模型调用，
    因此它可以在成本几乎为零的前提下，把"编造"从"概率性风险"变成"确定性拦截"。
    """
    items = list(payloads or [])
    report = verify_payloads(items, chunks, min_chars)
    blocked_idx = set(report.blockable(require_quote=require_quote))
    kept = [p for i, p in enumerate(items) if i not in blocked_idx]
    blocked = [p for i, p in enumerate(items) if i in blocked_idx]
    return kept, blocked, report


def describe(report: CitationReport, blocked: Sequence[dict] | None = None) -> str:
    """一行人类可读摘要，用于 `on_event` 与日志。"""
    n_given = len(report.checks) - report.n_missing
    parts = [
        f"引用 {n_given} 条",
        f"逐字 {report.n_exact}",
        f"归一 {report.n_normalized}",
        f"无法定位 {report.n_fabricated}",
        f"过短 {report.n_too_short}",
        f"未给引用 {report.n_missing}",
    ]
    if blocked is not None:
        parts.append(f"拦截 {len(blocked)} 题")
    return " · ".join(parts)


def iter_fabricated(report: CitationReport) -> Iterable[QuoteCheck]:
    """疑似编造的引用（供人工复盘 / 事件详情展示）。"""
    return (c for c in report.checks if c.fabricated)
