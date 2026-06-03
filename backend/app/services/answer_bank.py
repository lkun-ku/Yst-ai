"""答案库（Answer Bank）—— **独立于知识库**的一条检索路径（方案 B）。

## 为什么要有它

用户提供了 2011–2026 年的真题与解析，希望答题/批改时**优先依据真题与满分答案**。
但既定决策 **ADR-0003 是「真题原文不入库」**。两者取平衡的方案是 **B**：

| | 知识库（`data/official/`） | 答案库（`data/answer_bank/`） |
| --- | --- | --- |
| 被 `kb_corpus` 自动摄入 | **是**（`rglob("*.md")`） | **否**（在 `official_kb_dir` 之外） |
| 权威级别 | 官方 | **半官方**（教辅整理） |
| 用途 | 检索增强生成（RAG）的正式语料 | 答题/批改时**优先**参考 |

于是官方语料保持纯净，既有引用校验的口径也不被破坏；而"优先依据真题"由
**检索顺序**实现：`retrieve_for_question()` 先查答案库，命中不足再回落官方语料。

## ⚠️ 两条硬约束

1. **命中必须标注权威级别**：答案库是教辅整理，**不能冒充官方原文**。返回的每条
   都带 `authority="半官方"`，上游若引用它，应如实标注。
2. **引用仍需能子串校验**：返回的 `content` 必须包含被引用的原文 —— 否则
   `citation` 的子串校验会判为编造（这是我们防幻觉的硬机制，不能绕）。

## 为什么用关键词匹配而不是向量

embedding 供应商（百炼）当前欠费，`embed_one` 会**静默退回 64 维伪向量**
（`strict_embed` 已如实记 failed）。用伪向量做相似度等于自欺，所以这里用
**确定性关键词匹配**（CJK 二元切分 + 词频打分）：不依赖外部服务、结果可复现。
等 embedding 可用时，再叠加向量召回作为补充信号。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import settings

#: 只保留汉字/字母数字，用于切词（标点对匹配无益，反而制造噪声）
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


def bank_dir() -> Path | None:
    """答案库目录；未配置时用仓库内的 `data/answer_bank`。不存在则返回 None。"""
    if not settings.answer_bank_enabled:
        return None
    root = settings.answer_bank_dir or str(
        Path(__file__).resolve().parents[2] / "data" / "answer_bank"
    )
    p = Path(root)
    return p if p.is_dir() else None


def _tokens(text: str) -> list[str]:
    """中文按**二元切分**（无分词器时的稳妥做法），英文数字按词。"""
    out: list[str] = []
    for seg in _TOKEN_RE.findall(text or ""):
        if "\u4e00" <= seg[0] <= "\u9fff":
            out.extend(seg[i : i + 2] for i in range(len(seg) - 1)) if len(seg) > 1 else out.append(seg)
        else:
            out.append(seg.lower())
    return out


def load_entries() -> list[dict]:
    """读取答案库下所有 `*.json`，返回扁平条目列表。

    条目形态（由 `scripts/build_answer_bank.py` 产出）：
    `{"id","type","stem","options","answer","points","reference","source"}`
    """
    d = bank_dir()
    if d is None:
        return []
    entries: list[dict] = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # 单个文件坏了不该拖垮整库
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    entries.append({**it, "_bank_file": f.name})

    return entries


def _entry_text(e: dict) -> str:
    """条目 → 可被引用的文本（题干 + 选项 + 答案 + 采分点）。"""
    parts = [e.get("stem") or ""]
    for o in e.get("options") or []:
        if isinstance(o, dict):
            parts.append(f"{o.get('key')}.{o.get('text') or ''}")
    if e.get("answer"):
        parts.append("答案：" + "".join(str(a) for a in e["answer"]))
    for p in e.get("points") or []:
        parts.append("采分点：" + str(p))
    if e.get("reference"):
        parts.append("参考作答：" + str(e["reference"]))
    return "\n".join(x for x in parts if x)


#: 规则类条目的 `id` 前缀。它们不是"某道真题"，而是**判分口径**（教辅转述的评分规则要点）。
#:
#: 为什么单列出来：规则条目**很短**（一两百字），而关键词打分带长度归一
#: （`score = hit / (1 + len ** 0.5)`）—— 规则天然抢不过上千字的满分答卷。
#: 可它恰恰是批改最该看到的东西（"按什么给分"比"某一年的满分答案"更普适）。
#: 所以 `marking.retrieve_rubric` 对同题型的规则**必带**，不参与排名竞争。
RULES_ID_PREFIX = "rule-"


def _as_chunk(e: dict, score: float = 0.0) -> dict:
    """条目 → 与检索切片**同形**的结果。**统一在这里盖权威级别的章**，避免各处漏标。"""
    text = _entry_text(e)
    return {
        "id": e.get("id"),
        "content": text,
        "heading_path": f"答案库/{e.get('_bank_file')}#{e.get('id')}",
        "char_count": len(text),
        "bank_score": round(score, 4),
        "authority": "半官方",  # ⚠️ 必带：禁止冒充官方
        "source_type": "answer_bank",
    }


def rule_entries(qtype: str) -> list[dict]:
    """取某题型的**评分规则**条目（`id` 以 `rule-` 开头、`type` 相符）。"""
    want = str(qtype or "").strip().lower()
    return [
        _as_chunk(e)
        for e in load_entries()
        if str(e.get("id") or "").startswith(RULES_ID_PREFIX)
        and str(e.get("type") or "").strip().lower() == want
    ]


def search_answer_bank(query: str, k: int = 8, types: set[str] | None = None) -> list[dict]:
    """在答案库里检索（关键词打分），返回**形状与切片一致**的结果，便于上游复用。

    `types`：只在这些题型里找（如 `{"material"}`）。**批改链路必须传它** ——
    答案库里同时躺着 254 道**单选题**，不筛题型就会把客观题的答案与解析
    当成主观题的"评分依据"，而那看起来完全正常（错得很安静）。
    """
    entries = load_entries()
    if types is not None:
        want = {str(t).strip().lower() for t in types}
        entries = [e for e in entries if str(e.get("type") or "").strip().lower() in want]
    if not entries:
        return []
    q = _tokens(query)
    if not q:
        return []
    qset: dict[str, int] = {}
    for t in q:
        qset[t] = qset.get(t, 0) + 1

    scored: list[tuple[float, dict]] = []
    for e in entries:
        text = _entry_text(e)
        toks = _tokens(text)
        if not toks:
            continue
        hit = sum(qset.get(t, 0) for t in set(toks))
        if hit == 0:
            continue
        # 长度归一：短条目更容易"看起来很相关"，压一压
        score = hit / (1 + len(toks) ** 0.5)
        scored.append((score, e))

    scored.sort(key=lambda x: -x[0])
    return [_as_chunk(e, score) for score, e in scored[:k]]


def retrieve_for_question(db, scope, query: str, k: int = 8, embed_fn=None) -> list[dict]:
    """**答题专用检索**：答案库优先，命中不足再回落官方语料。

    这是"优先依据真题"的落点 —— 不是把真题塞进知识库，而是**在检索顺序上优先**。
    答案库命中的条目带 `source_type="answer_bank"`，上游据此标注权威级别。
    """
    hits = search_answer_bank(query, k)
    if len(hits) >= k or db is None:
        return hits[:k]
    from .kb_retrieval import retrieve  # 延迟导入：避免与检索模块的循环依赖

    rest = k - len(hits)
    try:
        official = retrieve(db, query, scope, k=rest, embed_fn=embed_fn)
    except Exception:  # noqa: BLE001 — 官方检索失败不应让答题整体失败
        return hits
    for h in official or []:
        h.setdefault("authority", "官方")
        h.setdefault("source_type", "official")
    return hits + list(official or [])
