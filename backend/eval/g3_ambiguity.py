"""N3 负样本：**题干/选项的真实歧义** —— 两个**措辞不同**的选项都说得通。

## 它补的是 N1 / N2 补不了的那一半

| 负样本 | 怎么造的 | 真值 | 已测 |
| --- | --- | --- | --- |
| N1 | 改**答案键** | 确定（选项没动） | ✅ 拦截 1.0 |
| N2 | 把某错误选项的文本**改成与正确选项完全相同** | 确定 | ✅ |
| **N3（本文件）** | 让模型把正确选项**改写成措辞不同、含义同等正确**的另一句，顶替一个错误选项 | **构造确定，但"是否真的同义"需人工复核** | ⬜ |

N2 的边界此前写得很清楚：**「两个选项文本相同」是结构性歧义，不是真实试卷里那种
"两个不同措辞的选项都说得通"**。N3 就是为后者造的：答案键没动、也没有任何重复文本，
题面看起来完全合法 —— 只是**答哪个都对**。这才是 G3 最该拦、也最难拦的一类。

⚠️ **真值需人工复核**：改写到底同不同义，程序无法自动判定 ——
所以报告里逐条列出了「原正确选项 / 改写文本 / 盲答结果」，就是为了让人能核。
2026-06-12 首跑（20 题）已逐条复核过，20 条均判定为真正同义
（记录见 `改造计划.md` §5 第 12 项）。**若某条改写其实不等价，它就不该算 G3 的战绩** ——
少了这一步，这个负面结论会被"改写质量不行"的解释整个推翻。

⚠️ **与 `eval/g3_ambiguous_eval.py` 的关系**：那是**另一支** N3 —— 用预生成的改写数据集、
n=8（拦截 0.375）；本支在运行时改写、n=20（拦截 0.15）。两者**构造不同、结论同向**，
所以暂时并存。但**重复实现是债**：合并成一支是待办，否则"该复核哪一份"会变成新问题。

## 为什么读数习惯和基准表相反

这批题**本来就该被拦**，所以 `blocked_rate` **高才是对的**。为避免被误读成"误杀率"，
结果写成**独立的证据文件**（`g3_n3_ambiguity.md`），并在报告里自己声明。

## 两种失败信号分开统计（这是本表最有用的地方）

- **不一致**：N 次盲答彼此不同 → 模型在摇摆，它自己也拿不准；
- **一致但与答案键不符**：模型**稳定地**选了另一个（同样正确的）选项。

两者都导致拦截，但含义不同：前者是"不确定"，后者是"有第二个正确答案"。
只报一个总数会把这两种截然不同的情形混成一句话。

## 用法

    python eval/g3_ambiguity.py --limit 6            # 默认 fake，走机械改写（不花钱）
    $env:LLM_MODE='real'; python eval/g3_ambiguity.py --limit 20
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 与 `g3_eval.py` 同一条纪律：**默认 fake** —— 跑起来不花额度。
os.environ.setdefault("LLM_MODE", "fake")

from app.config import settings  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
from app.services.quality_gates import vote_uniqueness  # noqa: E402

_DATASET = pathlib.Path(__file__).resolve().parent / "datasets" / "g3_唯一性" / "官方题样本.json"
_OUT_DIR = pathlib.Path(__file__).resolve().parent / "results"

#: fake 模式下的确定性同义替换（也用于"模型改写失败"时的兜底）。
#: 每条都是**含义等价**的助动词/连词替换 —— 宁可替换幅度小，也不要改写歪了：
#: 一旦改写出两个并非等价的选项，本评测的真值就站不住了。
_SYNONYM_RULES = (
    ("应当", "应该"),
    ("不得", "不能"),
    ("可以", "可"),
    ("或者", "或"),
    ("以及", "和"),
    ("应当依法", "依法应当"),
    ("有权", "有权利"),
    ("必须", "须"),
)

_PARAPHRASE_PROMPT = """【同义改写】下面是一道教师资格证考试选择题的题干与其中一个正确选项。

题干：{stem}
正确选项：{text}

请把这个正确选项改写成一句**措辞明显不同、但含义完全等价**的表述。
用途：制造一道"两个选项都说得通"的歧义题，作为质量闸门评测的负样本。

要求：
1. 含义必须与原文完全等价 —— 不得改变适用范围、程度、肯否定或主体；
2. 措辞要明显不同（不能只换一个语气词），但不得引入原文没有的新信息；
3. 字数与原文相近；
4. 只输出改写后的那一句话，不要输出选项字母、引号或任何解释。"""


# ---------------- 构造（确定性部分，可单测） ----------------

def mechanical_paraphrase(text: str) -> str:
    """确定性的同义改写：只替换**一组**助动词/连词，保证含义等价。

    没有可用替换时返回 `""`（调用方据此跳过该题）——
    宁可少一道样本，也不要用"看着像改写其实是瞎改"的东西污染真值。
    """
    src = (text or "").strip()
    if not src:
        return ""
    for a, b in _SYNONYM_RULES:
        if a in src:
            out = src.replace(a, b, 1)
            return out if out != src else ""
    return ""


def clean_paraphrase(raw: str, original: str) -> str:
    """清洗模型输出：去选项标号（B. / B、）、去引号、只留第一句非空行。"""
    if not raw:
        return ""
    line = ""
    for ln in raw.splitlines():
        s = ln.strip().strip("　 ")
        if s:
            line = s
            break
    for mark in ("：", ":"):
        # 模型偶尔会写「改写：xxx」
        idx = line.find(mark)
        if 0 <= idx <= 6 and len(line) > idx + 1:
            line = line[idx + 1 :].strip()
    line = line.strip("\"'“”‘’ ")
    for prefix_len in (2, 3):
        head = line[:prefix_len]
        if len(line) > prefix_len + 1 and head[0] in "ABCD" and head[1] in ".、)":
            line = line[prefix_len:].strip()
            break
    if not line or line == (original or "").strip():
        return ""
    return line


def pick_wrong_option(payload: dict) -> dict | None:
    """挑一个**错误选项**作为被顶替的目标。找不到返回 None（该题不适用）。"""
    right = {str(k).strip().upper() for k in (payload.get("answer") or [])}
    for o in payload.get("options") or []:
        if str(o.get("key") or "").strip().upper() not in right:
            return o
    return None


def build_variant(payload: dict, paraphrase: str) -> dict | None:
    """把*某个错误选项*的文本换成译文 → 题面合法、答案键未动、但**有两个正确答案**。"""
    target = pick_wrong_option(payload)
    if not target or not paraphrase:
        return None
    new_opts = [
        ({**o, "text": paraphrase} if o is target else o) for o in (payload.get("options") or [])
    ]
    return {**payload, "options": new_opts}


def paraphrase_option(client, payload: dict) -> tuple[str, str]:
    """返回 `(改写文本, 来源)`；改造不出来时返回 `("", 原因)`。"""
    right = {str(k).strip().upper() for k in (payload.get("answer") or [])}
    src = next(
        (
            o
            for o in (payload.get("options") or [])
            if str(o.get("key") or "").strip().upper() in right
        ),
        None,
    )
    if not src:
        return "", "找不到正确选项"
    original = (src.get("text") or "").strip()
    if not original:
        return "", "正确选项无文本"

    if type(client).__name__ == "FakeLLMClient":
        out = mechanical_paraphrase(original)
        return (out, "mechanical") if out else ("", "无可用的确定性同义替换")

    raw = client.ask(
        _PARAPHRASE_PROMPT.format(stem=(payload.get("stem") or "")[:400], text=original)
    )
    out = clean_paraphrase(raw or "", original)
    if not out:
        return "", "模型未返回可用改写"
    return out, "llm"


# ---------------- 采集与记账 ----------------

@dataclass
class N3Metrics:
    n_total: int = 0        # 原始题数
    n_built: int = 0        # 成功造出歧义变体的
    n_skipped: int = 0
    n_valid: int = 0        # 至少一次有效盲答
    n_blocked: int = 0
    n_disagree: int = 0     # 失败信号①：N 次盲答彼此不一致（模型在摇摆）
    n_mismatch: int = 0     # 失败信号②：一致但与答案键不符（模型稳定地选了另一个正确项）

    @property
    def build_rate(self) -> float:
        return round(self.n_built / self.n_total, 4) if self.n_total else 0.0

    @property
    def blocked_rate(self) -> float:
        """**高才是对的**（本批题本该被拦）。"""
        return round(self.n_blocked / self.n_valid, 4) if self.n_valid else 0.0

    @property
    def disagree_rate(self) -> float:
        return round(self.n_disagree / self.n_valid, 4) if self.n_valid else 0.0

    @property
    def mismatch_rate(self) -> float:
        return round(self.n_mismatch / self.n_valid, 4) if self.n_valid else 0.0

    def as_row(self) -> dict:
        return {
            "n_total": self.n_total,
            "n_built": self.n_built,
            "n_skipped": self.n_skipped,
            "build_rate": self.build_rate,
            "n_valid": self.n_valid,
            "blocked_rate": self.blocked_rate,
            "disagree_rate": self.disagree_rate,
            "mismatch_rate": self.mismatch_rate,
        }


def _write_markdown(path: pathlib.Path, result: dict) -> None:
    m = result["metrics"]
    meta = result["metadata"]
    lines = [
        "# G3 负样本 N3 · 真实歧义（不同措辞的两个选项都说得通）",
        "",
        f"- 生成时间：{meta['time']}　原始题 {m['n_total']}　造出变体 {m['n_built']}（{m['build_rate']}）"
        f"　跳过 {m['n_skipped']}　有效投票 {m['n_valid']}",
        f"- 模型：LLM={meta['llm']}　每次投票 {meta['votes']} 次　改写来源：{meta['paraphrase_source']}",
        "",
        "> ⚠️ **读数方向与基准表相反**：这批题**本来就该被拦**，`blocked_rate` **高才是对的**。",
        "> 它不是误杀率 —— 误杀率请用 `g3_baseline.md`。",
        "",
        "| 指标 | 值 | 读法 |",
        "| --- | --- | --- |",
        f"| **blocked_rate（拦截率）** | **{m['blocked_rate']}** | **高才是对的**：闸得住真实歧义 |",
        f"| disagree_rate（N 次盲答不一致） | {m['disagree_rate']} | 模型自己也在摇摆 |",
        f"| mismatch_rate（一致但与答案键不符） | {m['mismatch_rate']} | 模型**稳定地**选了另一个正确选项 |",
        f"| build_rate（成功造出歧义变体） | {m['build_rate']} | 低 → 构造手法覆盖不到这类题 |",
        "",
        "> 前两种失败信号都导致拦截，但含义不同：前者是「拿不准」，后者是「有第二个正确答案」。",
        "> 只报总数会把它们混成一句 —— 而它们的处置策略其实不一样。",
        "",
        "## 逐题记录（**真值要能复核**）",
        "",
        "> 「是否真的同义」不是 gzip 能自动判定的事：改写文本与盲答结果都列在这里，供人工复核。",
        "> 若某条改写其实不等价，它就不该算 G3 的战绩 —— 请把它标记为 `可疑`。",
        "",
        "| # | 期望答案 | 盲答 | 一致 | 相符 | G3 | 失败信号 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in result.get("samples") or []:
        lines.append(
            f"| {s['id']} | {s['expect']} | {s['votes']} | {s['agree']} | {s['matches']} | "
            f"{'放行' if s['passed'] else '**拦截**'} | {s['signal']} |"
        )
    lines += ["", "### 改写明细", ""]
    for s in result.get("samples") or []:
        lines += [
            f"- #{s['id']}　{s['stem']}",
            f"  - 原正确选项：{s['original']}",
            f"  - 改写（{s['source']}）：{s['paraphrase']}　→　顶替了选项 {s['replaced']}",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------- 主流程 ----------------

def main(limit: int = 20) -> dict:
    from datetime import datetime

    if not _DATASET.exists():
        raise SystemExit(f"评测集不存在：{_DATASET}")
    items = (json.loads(_DATASET.read_text(encoding="utf-8")).get("items") or [])[:limit]
    if not items:
        raise SystemExit("评测集为空")

    client = get_llm_client()
    mode_llm = "fake" if type(client).__name__ == "FakeLLMClient" else "real"
    print(f"题数 {len(items)}　LLM={mode_llm}　每次投票 {settings.gate_g3_votes} 次", flush=True)

    m = N3Metrics(n_total=len(items))
    samples: list[dict] = []
    sources: set[str] = set()

    for i, it in enumerate(items):
        if i and i % 5 == 0:
            print(f"    [{i}/{len(items)}] …", flush=True)
        para, why = paraphrase_option(client, it)
        if not para:
            m.n_skipped += 1
            print(f"    #{it.get('id')} 跳过：{why}", flush=True)
            continue
        sources.add(why if why in ("mechanical", "llm") else "llm")
        variant = build_variant(it, para)
        if variant is None:
            m.n_skipped += 1
            continue
        m.n_built += 1

        r = vote_uniqueness(client, variant, settings.gate_g3_votes)
        if r.n == 0:
            continue  # 投票全无效 → 无法判定，不计入比率
        m.n_valid += 1
        if not r.agree:
            m.n_disagree += 1
        if not r.matches:
            m.n_mismatch += 1
        if not r.passed:
            m.n_blocked += 1
        samples.append({
            "id": it.get("id"),
            "stem": (it.get("stem") or "")[:70],
            "expect": list(it.get("answer") or []),
            "votes": [list(v) for v in r.votes],
            "agree": r.agree,
            "matches": r.matches,
            "passed": r.passed,
            "signal": "摇摆" if not r.agree else ("选了另一个" if not r.matches else "—"),
            "original": next(
                (
                    o.get("text")
                    for o in (it.get("options") or [])
                    if str(o.get("key") or "").strip().upper()
                    in {str(k).strip().upper() for k in (it.get("answer") or [])}
                ),
                "",
            ),
            "paraphrase": para,
            "source": why,
            "replaced": (pick_wrong_option(it) or {}).get("key"),
        })

    row = m.as_row()
    print(
        f"    造出 {row['n_built']}/{row['n_total']} | **拦截 {row['blocked_rate']}** | "
        f"摇摆 {row['disagree_rate']} | 选了另一个 {row['mismatch_rate']}"
    )
    result = {
        "metrics": row,
        "samples": samples,
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "llm": mode_llm,
            "votes": settings.gate_g3_votes,
            "dataset": str(_DATASET.name),
            "paraphrase_source": ",".join(sorted(sources)) or "(无)",
            "framing": "N3 负样本 · 真实歧义 —— **拦截率高才是对的**（与误杀率表读法相反）",
        },
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = _OUT_DIR / "g3_n3_ambiguity.md"
    # 同样的守卫：**不许用 fake 覆盖真实数据**（这条是在 g3_eval 上踩出来的）
    if mode_llm == "fake" and md.exists() and "LLM=real" in md.read_text(encoding="utf-8"):
        md = md.with_name(f"{md.stem}_fake.md")
        print(f"    已有真实结果，本次 fake 改写到 {md.name}（不覆盖）")
    _write_markdown(md, result)
    (md.with_suffix(".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写入：{md}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="用多少道原始题（默认 20）")
    args = ap.parse_args()
    main(limit=args.limit)
