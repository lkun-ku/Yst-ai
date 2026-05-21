"""G3 的 N3 负样本：**真实的**歧义题 —— 两个**不同措辞**的选项都说得通。

## 为什么还要再来一类负样本

N1（答案键改错）与 N2（两个选项文本一模一样）都测过了，但**两类都没能触发** G3 的
「多次盲答不一致」这条路径（N2 的 `agree` 恒为 **1.0** —— 模型对"两个一模一样的选项"非常确定）。
而那条路径恰恰是 `quality_gates.py` 设计里发现歧义的**主要手段**。于是只剩一句只能靠猜的话：

> 对真实试卷里那种「两个**措辞不同**的选项都说得通」的歧义，这条路会触发吗？

本脚本把它变成可测的：用模型把一道单选题的**某个错误选项改写成同样成立的说法**
（换措辞、不与原正确项重复）→ 这道题**真的有两个正确答案** → 真值确定（该被拦），
且它**不是**靠"复制文本"造出来的（那正是 N2 的局限）。

## 关键指标：`agree_false_rate`

前两类负样本都没让 `agree` 变成 False。所以本表的**核心一行**就是它：
**「多次盲答不一致」这条路径终于被触发的比例**。它若仍然为 0，那说明这条路在真实歧义上
同样不工作（一个明确的负结论，比含糊的"应该能发现"有用得多）。

## 一处必须写清的边界

「改写的选项确实也成立」这个**真值来自模型判断**，不是人判的 —— **有循环论证的风险**。
所以把改写后的选项原文**一并落盘**（`datasets/g3_歧义/`）供人工复核；本表只能说明
「在这批**模型认定**的歧义上 G3 表现如何」，**不能**说明「G3 能发现所有歧义」。

跑法（真实模式才有意义 —— fake 客户端不会真的改写）：

    python eval/g3_ambiguous_eval.py --limit 12        # 默认 fake，仅验证管线
    $env:LLM_MODE='real'; python eval/g3_ambiguous_eval.py --limit 12
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("LLM_MODE", "fake")

from app.config import settings  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
from app.services.quality_gates import vote_uniqueness  # noqa: E402

_SRC = pathlib.Path(__file__).resolve().parent / "datasets" / "g3_唯一性" / "官方题样本.json"
_OUT_DIR = pathlib.Path(__file__).resolve().parent / "results"
_MUTATED = pathlib.Path(__file__).resolve().parent / "datasets" / "g3_歧义" / "改写后的题.json"

#: 改写提示词。**必须要求"换措辞、不与原正确项重复"** —— 否则模型会直接复制正确选项，
#: 那就退化成 N2（两个一模一样的选项），测不出"措辞不同但都成立"的歧义。
_REWRITE_PROMPT = """【歧义题构造】下面是一道单选题。请把其中一个**错误选项**改写成**同样成立**的说法：
- 换一种措辞，表达另一个也正确的观点；
- **不要**与原正确选项的文字重复（重复就没意义了）；
- 改写后它应当与正确选项一样站得住脚。

只输出 JSON：{{"key": "被改写的选项键", "text": "改写后的选项文本", "why": "一句话说明它为什么也成立"}}

题干：{stem}
选项：{options}
原正确答案：{answer}
【歧义题构造】"""


def _rewrite(client, payload: dict) -> tuple[dict | None, str]:
    """把一道题的一个错误选项改写成"同样成立" → 得到一道**真有两个正确答案**的题。

    改写不可用（模型失败/解析失败）时返回 `(None, 原因)` —— 调用方跳过它，不当成"没被拦"。
    """
    opts = list(payload.get("options") or [])
    right = {str(k).strip().upper() for k in (payload.get("answer") or [])}
    target = next((o for o in opts if str(o.get("key") or "").strip().upper() not in right), None)
    if target is None:
        return None, "没有可改写的错误选项"
    prompt = _REWRITE_PROMPT.format(
        stem=payload.get("stem") or "",
        options="\n".join(f"{o.get('key')}. {o.get('text')}" for o in opts),
        answer="".join(sorted(right)),
    )
    text = client.ask(prompt)
    if not text:
        return None, "模型不可用"
    try:
        obj = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except Exception:  # noqa: BLE001
        return None, "输出无法解析"
    if not isinstance(obj, dict) or not obj.get("text"):
        return None, "输出缺字段"
    key = str(obj.get("key") or target.get("key") or "").strip().upper()
    new_opts = [
        {**o, "text": str(obj["text"])} if str(o.get("key") or "").strip().upper() == key else o
        for o in opts
    ]
    if new_opts == opts:
        return None, f"改写没有落到任何选项上（key={key}）"
    mutated = {**payload, "options": new_opts, "_ambiguous_key": key, "_why": str(obj.get("why") or "")}
    return mutated, f"把选项 {key} 改写成同样成立的说法"


def _metrics(rows: list[dict]) -> dict:
    valid = [r for r in rows if r["n"] > 0]
    if not valid:
        return {"n_items": len(rows), "n_valid": 0}
    n = len(valid)
    return {
        "n_items": len(rows),
        "n_valid": n,
        "agree_rate": round(sum(1 for r in valid if r["agree"]) / n, 4),
        #: **本表的核心一行**：G3 的「多次盲答不一致」这条路径终于被触发的比例。
        #: 前两类负样本（N1/N2）里它恒为 0；若这里仍为 0，是个明确的负结论。
        "agree_false_rate": round(sum(1 for r in valid if not r["agree"]) / n, 4),
        "matches_rate": round(sum(1 for r in valid if r["matches"]) / n, 4),
        "blocked_rate": round(sum(1 for r in valid if not (r["agree"] and r["matches"])) / n, 4),
    }


def _write_markdown(path: pathlib.Path, result: dict) -> None:
    m, meta = result["metrics"], result["metadata"]
    lines = [
        "# G3 · N3 负样本基准（**真实的**歧义题：两个措辞不同的选项都成立）",
        "",
        f"- 生成时间：{meta['time']}　题数 {m.get('n_items')}（有效 {m.get('n_valid')}）"
        f"　每次投票 {meta['votes']} 遍",
        f"- 模型：LLM={meta['llm']}　改写方式：模型把某个错误选项改写成同样成立的说法",
        f"- 改写后的题已落盘（供人工复核「它也成立」这个判断）：`datasets/g3_歧义/改写后的题.json`",
        "",
        "> ⚠️ **本表的读法与基准表相反**：这里 `blocked_rate` 高才是对的（那些题真的有两个正确答案）。",
        "",
        "| 指标 | 值 | 读法 |",
        "| --- | --- | --- |",
        f"| **agree_false_rate** | **{m.get('agree_false_rate')}** | **「多次盲答不一致」这条路径被触发的比例** —— N1/N2 里它恒为 0 |",
        f"| blocked_rate | {m.get('blocked_rate')} | 被 G3 拦下的比例（这里高才是对的） |",
        f"| matches_rate | {m.get('matches_rate')} | 盲答与**题目自带**答案相符的比例（真有两解时本就该偏低） |",
        f"| agree_rate | {m.get('agree_rate')} | 模型对改写后的题有多笃定 |",
        "",
        "## 逐题（含改写说明，供人工复核）",
        "",
    ]
    for r in result["items"]:
        lines.append(f"- `{r['id']}`　{r.get('rewrite', '')}")
        lines.append(
            f"  - 有效 {r.get('n')} · agree {r.get('agree')} · matches {r.get('matches')} · "
            f"盲答 {r.get('votes')}"
        )
    lines += [
        "",
        "## 边界（必须与数字一起引用）",
        "",
        "- 「改写的选项也成立」这个**真值来自模型判断**，不是人判的 —— **有循环论证的风险**；",
        "  改写原文已落盘供人工复核，本表只说明「在这批**模型认定**的歧义上表现如何」；",
        "- 样本来自官方池 PASSED 的单选题（前 12 题），**不是**真实试卷里的歧义题；",
        "- 因此**不能**外推成「G3 能/不能发现所有歧义」。",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(limit: int = 12) -> dict:
    from datetime import datetime

    data = json.loads(_SRC.read_text(encoding="utf-8"))
    items = (data.get("items") or [])[:limit]
    client = get_llm_client()
    mode_llm = "fake" if type(client).__name__ == "FakeLLMClient" else "real"
    print(f"题数 {len(items)}　LLM={mode_llm}　投票 {settings.gate_g3_votes} 遍", flush=True)
    if mode_llm == "fake":
        print("    ⚠️ fake 客户端不会真的改写 —— 本次只验证管线，数字无意义", flush=True)

    mutated: list[dict] = []
    for it in items:
        m, why = _rewrite(client, it)
        print(f"    [N3] {it.get('id')}：{why}", flush=True)
        if m:
            m["_rewrite"] = why
            mutated.append(m)
    if not mutated:
        raise SystemExit("没有一题改写成功 —— 无法评测")

    _MUTATED.parent.mkdir(parents=True, exist_ok=True)
    _MUTATED.write_text(
        json.dumps({"note": "N3：由模型把一个错误选项改写成同样成立的说法得到的歧义题；供人工复核", "items": mutated},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rows: list[dict] = []
    for it in mutated:
        rep = vote_uniqueness(client, it, settings.gate_g3_votes)
        rows.append({"id": it.get("id"), "rewrite": it.get("_rewrite", ""), **rep.as_dict()})
    metrics = _metrics(rows)

    result = {
        "metrics": metrics,
        "items": rows,
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "llm": mode_llm,
            "votes": settings.gate_g3_votes,
            "source": str(_SRC.name),
            "caveat": (
                "改写由**模型**完成，「它也成立」这个真值是模型判断而非人判 —— 有循环论证风险；"
                "改写原文已落盘供人工复核。样本非真实试卷歧义题，结论不可外推"
            ),
        },
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = _OUT_DIR / "g3_n3_baseline.md"
    if metrics.get("n_valid", 0) == 0:
        md = _OUT_DIR / "g3_n3_failed.md"
        print("    ⚠️ 没有一次有效投票（接口故障？）→ 不覆盖基准")
    elif mode_llm == "fake" and md.exists() and "LLM=real" in md.read_text(encoding="utf-8"):
        md = _OUT_DIR / "g3_n3_fake.md"
        print("    已有真实基准，本次 fake 结果改写到 g3_n3_fake.md")
    _write_markdown(md, result)
    (md.with_suffix(".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"    **不一致触发率 {metrics.get('agree_false_rate')}** | 拦截 {metrics.get('blocked_rate')} | "
        f"相符 {metrics.get('matches_rate')}"
    )
    print(f"\n已写入：{md}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12, help="用多少道题（默认 12）")
    args = ap.parse_args()
    main(limit=args.limit)
