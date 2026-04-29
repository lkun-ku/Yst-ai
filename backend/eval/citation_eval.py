"""引用闸门的成对度量：**拦截率** 与 **流出编造率**（改造计划 §3 第 4 项）。

`run_eval.py` 测生成质量与成本，`retrieval_eval.py` 测检索质量，本脚本测**引用校验本身**。

---

## 真值集怎么构造（以及为什么这样构造才不算循环论证）

两类用例，**各自的构造依据互相独立于被测量的判定逻辑**：

**正例（期望放行，`fabricated=False`）** —— 从法条语料**逐字摘录**，再做"只改写法不改内容"的
扰动：改标点、改断行、加书名号出处前缀。这些扰动是**人工声明的**"语义不变"，
不依赖校验器的实现。若闸门拦下了它们，就是**误杀**（假阴性），记为 `false_negative_rate`。

**反例（期望拦截，`fabricated=True`）** —— 对真实法条做**内容篡改**：换条号、换词、
删中间字、拼接两条、凭空编造。

> ⚠️ **反例的自检用「精确子串缺席」，不用校验器自己的归一化判定。**
> 若用后者来定义"这条不是原文"，就等于让被测对象自己出考卷 —— 凡是它认不出的
> 都算"编造"，漏放率恒为 0，测试失去意义。
> 这里改用最客观、最弱的判据：**这条字符串在原语料里精确地不存在**。
> 于是"闸门放行了它"就等价于"闸门宣称定位到了语料里根本没有的文字" —— 是真正的漏放。
> 不满足该判据的反例会被**跳过并计数**（宁缺毋滥），而不是硬算进指标。

## 语料窗口

每条用例只给校验器 **5 片**切片（含其来源片），而不是整个语料库 ——
这更接近生产：模型只看得到召回的 top-k，闸门也只该在**它看过的那些切片**里核对依据。
给全库会让通过变得过于容易，指标偏乐观。

运行：
    python backend/eval/citation_eval.py
    python backend/eval/citation_eval.py --out ../docs/eval
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from app.services.citation import (  # noqa: E402
    DEFAULT_MIN_QUOTE_CHARS,
    STATUS_EXACT,
    STATUS_NORMALIZED,
    verify_quote,
)
from app.services.kb_corpus import DEFAULT_ROOT, iter_source_files, plan_file  # noqa: E402

try:  # 兼容「脚本直接运行」与「pytest 包上下文」两种方式
    from .metrics import evaluate_citation_gate
except ImportError:  # pragma: no cover
    from eval.metrics import evaluate_citation_gate  # noqa: E402

#: 只取这两个目录（都有"条"这个强单元，引用边界清晰）。
_ARTICLE_DIRS = ("laws/", "regulations/")
#: 摘录长度：够长才能构成有效证据，也才能容纳"删中间字"这类篡改。
EXCERPT_CHARS = 40
#: 给校验器的切片窗口大小（含来源片）。
CONTEXT_WINDOW = 5
#: 每条法规最多产出多少组用例（控制规模，避免长法规主导统计）。
MAX_PER_DOC = 8

_ARTICLE_HEAD_RE = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+条)[\s　]*")

#: 可读字符（汉字 / 字母 / 数字）。**刻意在评测侧独立实现一份**，
#: 不复用 `citation.normalize_for_match` —— 用例构造不应依赖被测对象。
_READABLE_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")


def _readable(text: str) -> str:
    return "".join(_READABLE_RE.findall(text or ""))


def _content_changed(before: str, after: str) -> bool:
    """篡改是否**真的动了内容**（可读字符层面）。

    只改标点 / 空白 / `#` 的"篡改"内容其实没变，它按定义是**正例**，
    计为漏放会冤枉校验器。这条守卫来自一次真实踩坑：
    首版"删中间字"用例恰好只删掉了 `#` 与换行，归一化后与原句完全相同 ——
    闸门放行是正确的，但指标把它记成了漏放（流出编造率 0.0054）。
    """
    return _readable(before) != _readable(after)

#: 凭空编造的模板：与真实法条措辞相近、但内容不存在。
#: 用"教师可以……"这类**看起来合理**的句子，比乱码更接近真实幻觉的形态。
_FABRICATED_TEMPLATES = (
    "第八十七条 教师有权自行决定教学内容并免除学生期末考试。",
    "第一百二十条 教师资格证在全国范围内永久有效，无需定期注册。",
    "第五十六条 学校可以根据自身情况取消学生的体育与艺术课程。",
    "第七十三条 学生在校期间的一切伤害事故均由学校承担全部责任。",
)


def load_law_chunks(root: str | Path | None = None) -> list[dict]:
    """把法条 Markdown 按 `kb_corpus` 的规则切成片（**不落库**，纯内存）。

    直接复用 `plan_file` 而不是另写一套切分 —— 评测必须建在**生产同一套切片**上，
    否则测的是另一个系统。
    """
    root_path = Path(root) if root else DEFAULT_ROOT
    chunks: list[dict] = []
    for rel, abs_path in iter_source_files(root_path):
        if not rel.startswith(_ARTICLE_DIRS):
            continue
        raw = abs_path.read_text(encoding="utf-8")
        for plan in plan_file(rel, raw):
            chunks.append(
                {"content": plan.content, "heading_path": plan.heading_path, "doc": rel}
            )
    for i, c in enumerate(chunks, 1):
        c["id"] = i
    return chunks


def _excerpt(content: str) -> str:
    """取切片正文前缀作为摘录 —— 必然是原文的精确子串。"""
    return (content or "").strip()[:EXCERPT_CHARS]


def _split_article(text: str) -> tuple[str, str]:
    """拆出开头的条号与其余正文（`第七条 未成年人的…` → `("第七条", "未成年人的…")`）。"""
    m = _ARTICLE_HEAD_RE.match(text or "")
    if not m:
        return "", text or ""
    return m.group(1), text[m.end():]


def _law_of(chunk: dict) -> str:
    """从 `heading_path`（`法名 / 章 / 条`）取法名。"""
    return (chunk.get("heading_path") or "").split(" / ")[0]


def _window(chunks: list[dict], i: int) -> list[dict]:
    """来源片及其后 4 片（环形），模拟"召回 top-5"的上下文。"""
    n = len(chunks)
    return [chunks[(i + k) % n] for k in range(min(CONTEXT_WINDOW, n))]


def _absent_exact(text: str, corpus_contents: list[str]) -> bool:
    """独立真值判据：该字符串**精确地**不在原语料中出现。

    刻意不使用 `citation.normalize_for_match` —— 理由见模块 docstring。
    """
    return bool(text) and not any(text in c for c in corpus_contents)


def build_cases(chunks: list[dict], excerpt_chars: int = EXCERPT_CHARS) -> tuple[list[dict], dict]:
    """构造正例 / 反例。返回 `(cases, stats)`。

    `stats` 记录各类别产出条数与被跳过的反例数，使"某类用例为 0"不会被误读成"该类全过"。
    """
    corpus_contents = [c.get("content") or "" for c in chunks]
    cases: list[dict] = []
    skipped: dict[str, int] = {}

    def add(quote: str, fabricated: bool, kind: str, ctx: list[dict]) -> None:
        cases.append({"quote": quote, "fabricated": fabricated, "kind": kind, "chunks": ctx})

    def add_negative(
        quote: str, kind: str, ctx: list[dict], original: str | None = None
    ) -> None:
        """反例必须同时满足两条才计入，否则跳过并计数（宁缺毋滥）：

        ① **缺席自检** —— 该字符串在语料中精确不存在（独立真值判据，见模块 docstring）；
        ② **内容确实被改动** —— 可读字符与原文不同（`original=None` 表示凭空编造，无原文可比）。
        """
        if original is not None and not _content_changed(original, quote):
            skipped[kind] = skipped.get(kind, 0) + 1
            return
        if _absent_exact(quote, corpus_contents):
            add(quote, True, kind, ctx)
        else:
            skipped[kind] = skipped.get(kind, 0) + 1

    # 每部法规均匀取样，避免最长的那部主导统计
    by_doc: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        by_doc.setdefault(c.get("doc") or "?", []).append(i)
    picked: list[int] = []
    for idxs in by_doc.values():
        step = max(1, len(idxs) // MAX_PER_DOC)
        picked.extend(idxs[::step][:MAX_PER_DOC])
    picked.sort()

    articles = [_split_article(c.get("content") or "")[0] for c in chunks]

    for i in picked:
        chunk = chunks[i]
        content = chunk.get("content") or ""
        quote = _excerpt(content)
        if len(quote) < 12:
            continue
        ctx = _window(chunks, i)
        article, rest = _split_article(quote)
        if not article or not rest:
            continue

        # ---- 正例：逐字 + 三种"只改写法"扰动（声明为语义不变） ----
        add(quote, False, "逐字摘录", ctx)
        if "，" in quote or "。" in quote:
            add(
                quote.replace("，", ",").replace("。", ".").replace("；", ";"),
                False,
                "改标点",
                ctx,
            )
        add(rest[:14] + "\n" + rest[14:], False, "改断行", ctx)
        # 带出处前缀：把条号提到前缀里、正文里去掉（这是模型最自然的写法）
        add(f"《{_law_of(chunk)}》{article}：{rest}", False, "出处前缀", ctx)

        # ---- 反例：内容篡改（每条都要过缺席自检 + 内容确实改动） ----
        # ① 换条号：正文不动，只把条号换成别处的 —— 这是法条场景最致命的错配
        other = next((a for a in articles if a and a != article), "")
        if other:
            add_negative(f"{other} {rest}", "换条号", ctx, original=quote)
        # ② 换词：把"应当"改成"可以"（法律含义直接反转），没有则替换中段两字
        if "应当" in rest:
            add_negative(f"{article} {rest.replace('应当', '可以', 1)}", "换词", ctx, original=quote)
        else:
            mid = max(1, len(rest) // 2)
            add_negative(
                f"{article} {rest[:mid]}可以{rest[mid + 2:]}", "换词", ctx, original=quote
            )
        # ③ 删中间字：挖掉中段 4 字，破坏其连续性
        if len(rest) > 14:
            mid = len(rest) // 2
            add_negative(f"{article} {rest[:mid]}{rest[mid + 4:]}", "删中间字", ctx, original=quote)
        # ④ 拼接：本条前半 + 另一条后半（各自都是原文，拼起来不是）
        other_chunk = chunks[(i + 7) % len(chunks)]
        other_excerpt = _excerpt(other_chunk.get("content") or "")
        if len(other_excerpt) >= 20 and len(rest) >= 20:
            add_negative(
                f"{article} {rest[:16]}{other_excerpt[-16:]}", "拼接两条", ctx, original=quote
            )

    # ⑤ 凭空编造：类型用例，全库一次即可（无原文可比，只过缺席自检）
    for tmpl in _FABRICATED_TEMPLATES:
        add_negative(tmpl, "凭空编造", chunks[:CONTEXT_WINDOW])

    # 注：「过短引用」（如只写"第七条"）**刻意不列为反例** —— 它在语料里精确存在，
    # 按真值定义它是真实引用，不是编造。它由 `citation_min_quote_chars` 这条**产品政策**
    # 拦下（过短不能作为证据），属于策略而非真假判定，故不纳入本指标
    # （该行为由 tests/test_citation.py 的单元用例覆盖）。

    stats = {
        "n_positives": sum(1 for c in cases if not c["fabricated"]),
        "n_negatives": sum(1 for c in cases if c["fabricated"]),
        "skipped_negatives": sum(skipped.values()),
        "skipped_by_kind": skipped,
        "n_chunks": len(chunks),
    }
    return cases, stats


def _verify(quote: str, chunks: list[dict]) -> bool:
    """被测函数：与生产闸门**同一个** `verify_quote`（不重写一份近似实现）。"""
    return verify_quote(quote, chunks).ok


def main(out: str | None = None) -> dict:
    chunks = load_law_chunks()
    cases, stats = build_cases(chunks)
    metrics = evaluate_citation_gate(cases, _verify)

    # 分层明细：哪一类正例被误杀、哪一类反例被漏放 —— 定位问题时最需要的信息
    detail: dict[str, dict] = {}
    for c in cases:
        d = detail.setdefault(c["kind"], {"n": 0, "passed": 0, "expected_pass": not c["fabricated"]})
        d["n"] += 1
        d["passed"] += 1 if _verify(c["quote"], c["chunks"]) else 0

    statuses = [verify_quote(c["quote"], c["chunks"]).status for c in cases]
    n_exact = sum(1 for s in statuses if s == STATUS_EXACT)
    n_norm = sum(1 for s in statuses if s == STATUS_NORMALIZED)

    print(f"语料切片：{stats['n_chunks']}　用例：{metrics.n_total}"
          f"（正例 {stats['n_positives']} / 反例 {stats['n_negatives']}）")
    if stats["skipped_negatives"]:
        print(f"⚠️ 跳过反例 {stats['skipped_negatives']} 条（未通过缺席自检）：{stats['skipped_by_kind']}")
    print()
    print(f"{'类别':<12}{'用例':>5}{'放行':>6}{'期望':>7}")
    for kind, d in detail.items():
        print(f"{kind:<12}{d['n']:>5}{d['passed']:>6}{'放行' if d['expected_pass'] else '拦截':>7}")
    print()
    print(f"命中层级：逐字 {n_exact} · 归一化 {n_norm} "
          f"（逐字占比 {round(n_exact / max(1, n_exact + n_norm), 4)}）")
    print(f"拦截率　　：{metrics.interception_rate}（{metrics.n_intercepted}/{metrics.n_known_fabricated}）")
    print(f"流出编造率：{metrics.leaked_fabrication_rate}（{metrics.n_leaked_fabricated}/{metrics.n_passed}）")
    print(f"误杀率　　：{metrics.false_negative_rate}")
    print(f"成对结论　：{'✅ 拦截率 > 0 且流出编造率 = 0' if metrics.sound else '❌ 未达标'}")

    # 把失败样本打出来：指标变差时，最需要的是"差在哪一条"，而不是又看一眼汇总数字
    leaks = [c for c in cases if c["fabricated"] and _verify(c["quote"], c["chunks"])]
    false_neg = [c for c in cases if not c["fabricated"] and not _verify(c["quote"], c["chunks"])]
    for label, group in (("漏放", leaks), ("误杀", false_neg)):
        if group:
            print(f"\n⚠️ {label}示例（{len(group)} 条，最多列 3 条）")
            for c in group[:3]:
                print(f"   [{c['kind']}] {c['quote'][:70]!r}")

    out_dir = out or os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(out_dir, exist_ok=True)
    result = {
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "excerpt_chars": EXCERPT_CHARS,
            "context_window": CONTEXT_WINDOW,
            "min_quote_chars": DEFAULT_MIN_QUOTE_CHARS,
            "n_chunks": stats["n_chunks"],
            "n_cases": metrics.n_total,
            "n_positives": stats["n_positives"],
            "n_negatives": stats["n_negatives"],
            "skipped_negatives": stats["skipped_negatives"],
            "truth_rule": "反例真值 = 该字符串在语料中精确不存在（独立于校验器的归一化判定）",
        },
        "metrics": metrics.as_row(),
        "by_kind": detail,
        "levels": {"exact": n_exact, "normalized": n_norm},
    }
    _write_markdown(os.path.join(out_dir, "citation_baseline.md"), result)
    with open(os.path.join(out_dir, "citation_baseline.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n已写入：{os.path.join(out_dir, 'citation_baseline.md')}")
    return result


def _write_markdown(path: str, result: dict) -> None:
    meta, m, detail = result["metadata"], result["metrics"], result["by_kind"]
    lines = [
        "# 引用闸门基准（source_quote 子串硬校验）",
        "",
        f"- 生成时间：{meta['time']}　语料 {meta['n_chunks']} 片　用例 {meta['n_cases']} 条"
        f"（正例 {meta['n_positives']} / 反例 {meta['n_negatives']}）",
        f"- 语境：每条用例给 {meta['context_window']} 片切片　摘录 {meta['excerpt_chars']} 字"
        f"　最短引用 {meta['min_quote_chars']} 字",
        f"- 真值规则：{meta['truth_rule']}",
        "",
        "> **成对读法**：只看「流出编造率 = 0」是自欺 —— 闸门未触发时它同样是 0。",
        "> 必须同时看「拦截率 > 0」：前者证明拦得住，后者证明确实在拦。",
        ">",
        "> **误杀率为 0 的含义有限**：本组正例是四类**人工设计的**写法扰动，",
        "> 它只证明这些容忍度被完整覆盖，**不等于**真实场景零误杀 ——",
        "> 真实模型的改写方式比这几类更多（概括、跨句合并、意译），",
        "> 那些情形会被子串判定拦下，这正是"宁可误杀、不可漏放"的代价。",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 拦截率（拦下 / 已知编造） | {m['interception_rate']}（{m['intercepted']}/{m['known_fabricated']}） |",
        f"| 流出编造率（流出 / 通过） | {m['leaked_fabrication_rate']}（{m['leaked']}/{m['passed']}） |",
        f"| 误杀率（真实引用被拦） | {m['false_negative_rate']} |",
        f"| 成对结论 | {'达标' if m['sound'] else '未达标'} |",
        f"| 命中层级 | 逐字 {result['levels']['exact']} · 归一化 {result['levels']['normalized']} |",
        "",
        "> 「过短引用」（如只写「第七条」）**刻意不计入本指标**：它在语料中精确存在，",
        "> 按真值定义属真实引用而非编造；它由 `citation_min_quote_chars` 这条产品政策拦下，",
        "> 属策略而非真假判定。",
        "",
        "## 分类明细",
        "",
        "| 类别 | 用例 | 放行 | 期望 |",
        "| --- | --- | --- | --- |",
    ]
    for kind, d in detail.items():
        lines.append(f"| {kind} | {d['n']} | {d['passed']} | {'放行' if d['expected_pass'] else '拦截'} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="结果输出目录（默认 eval/results/）")
    args = ap.parse_args()
    main(out=args.out)
