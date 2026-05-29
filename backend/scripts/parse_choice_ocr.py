"""把 OCR 出来的单选题文本，结构化成 G3 用的评测集。

## 输入与口径

- 来源：教辅整理的**扫描版真题**（`西米学府团队`），经 `ocr_pdf_to_text.py` OCR 后
  的 `.txt`（题面一本、答案一本）。
- **权威级别 = 半官方**：它是教辅的示范答案与「答案速查表」，**不是**官方发布
  （官方从不公布真题与评分细则）。产物里必须带上这个标注。
- **只存评测必需字段**（题干 / 选项 / 答案 / 年份 / 页码），**不入库**（`data/official/`），
  也不存解析正文 —— 版权与"真题原文不入库"这条既定决策都要求如此。

## 三处必须小心的地方（都是实测出来的）

1. **题号在每份试卷里重新从 1 开始** —— 22 份卷 × 29 题。所以只能**先按试卷分段**，
   再在卷内按题号合并。全局按题号会全部错位。
2. **答案来自「答案速查表」**（`序号12345678答案CBDBDDCA…`），**不依赖正文解析** ——
   正文是 OCR 过的长段落，错字多；速查表是短串，可靠性高得多。
3. **OCR 有非零错误率**（实测题号会出现 `1,2,2,4…` 这种重复/跳号）。因此：
   - 合并**按题号对齐**，不合格的题**丢弃而不位移**（一旦位移，后面所有答案全错）；
   - 每题都要过校验：恰好 4 个选项、答案 ∈ ABCD、题干长度达标；
   - **丢弃率必须报告** —— 它既是质量信号，也是"这批数据能不能用"的依据。

## 用法

    python scripts/parse_choice_ocr.py <题面.txt> <答案.txt> <输出.json> [--report 报告.md]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: 试卷头：`2025年上半年&…《综合素质》` —— 捕获年份与上下半年，用于分期对齐。
_EXAM_HEADER_RE = re.compile(r"(\d{4})年([上下])半年[^《]{0,60}《综合素质》")

#: 题号标记：`29．` / `30.` / `1、`
_QNO_RE = re.compile(r"(\d{1,2})[．.、]")

#: 选项标记：`A.` / `B、`。用它切出 A/B/C/D 四段。
_OPT_RE = re.compile(r"[ABCD][.．、]")

#: **依赖图/表**的题干（OCR 拿不到图）。这类题在纯文本形态下**根本不可答** ——
#: 若把它们留在评测集里，模型答错会被记成"闸门的误杀"，那是对 G3 的错判。
#: 实测：被拦的 6 道里就有 1 道是含图题（剪纸画辨民族服饰）。
_IMAGE_HINT_RE = re.compile(r"图(?!书馆)|如图|下图|剪纸画|漫画|照片|图片|所示|表中的|图形")

_KEYS = ("A", "B", "C", "D")


def split_exams(text: str) -> list[dict]:
    """按试卷头切分 → `[{year, half, body}]`。头部之前的内容（封面等）丢弃。"""
    parts = _EXAM_HEADER_RE.split(text or "")
    out: list[dict] = []
    for i in range(1, len(parts), 3):
        if i + 2 >= len(parts):
            break
        out.append({"year": int(parts[i]), "half": parts[i + 1], "body": parts[i + 2]})
    return out


def _pages(body: str) -> list[tuple[int, str]]:
    """`===== page N =====` → `[(页码, 该页文本)]`（保留页码是为了出错能回到原页核对）。"""
    chunks = re.split(r"^===== page (\d+) =====$", body or "", flags=re.MULTILINE)
    out: list[tuple[int, str]] = []
    for i in range(1, len(chunks), 2):
        out.append((int(chunks[i]), chunks[i + 1] if i + 1 < len(chunks) else ""))
    return out


def parse_questions(body: str) -> dict[int, dict]:
    """一份试卷的题面 → `{题号: {stem, options, page}}`。

    按题号对齐是刻意的：OCR 会漏号/重号，**丢弃而不位移**。
    """
    items: dict[int, dict] = {}
    for page_no, page_text in _pages(body):
        marks = list(_QNO_RE.finditer(page_text))
        for idx, m in enumerate(marks):
            start = m.end()
            end = marks[idx + 1].start() if idx + 1 < len(marks) else len(page_text)
            seg = page_text[start:end].strip()
            if not seg:
                continue
            opts = _split_options(seg)
            if opts is None:
                continue  # 选项数不对 → 丢（不位移）
            no = int(m.group(1))
            if no in items:
                continue  # 重复题号：保留先出现的
            items[no] = {"stem": opts["stem"], "options": opts["options"], "page": page_no}
    return items


def _split_options(seg: str) -> dict | None:
    """从 `题干…A.xxx B.xxx C.xxx D.xxx` 切出题干与 4 个选项；选项数不为 4 返回 None。"""
    marks = list(_OPT_RE.finditer(seg))
    if len(marks) != 4:
        return None
    stem = seg[: marks[0].start()].strip()
    options = []
    for i, m in enumerate(marks):
        s = m.end()
        e = marks[i + 1].start() if i + 1 < len(marks) else len(seg)
        options.append({"key": _KEYS[i], "text": seg[s:e].strip()})
        if _KEYS[i] != m.group(0)[0]:
            return None  # 标记顺序不是 A/B/C/D → 不信任
    if len(stem) < 10:
        return None
    if _IMAGE_HINT_RE.search(stem):
        return None  # 依赖图/表 → 纯文本下不可答，剔除（否则会被误记成闸门误杀）
    if any(not o["text"] for o in options):
        return None
    return {"stem": stem, "options": options}


def parse_answer_sheet(body: str) -> dict[int, str]:
    """解析「答案速查表」→ `{题号: 答案字母}`。

    速查表形态（OCR 后是连串）：`序号12345678答案CBDBDDCA序号910111213141516答案BAABCCDA…`
    解析办法：按 `序号…答案` 成对切分，把**序号串**与**答案串**按字符一一配对。
    若两者长度不等，说明 OCR 吃错了字 → 该段**整段丢弃**（宁可少要几个答案，也不能配对错位）。
    """
    out: dict[int, str] = {}
    for m in re.finditer(r"序号\s*([\d]+)\s*答案\s*([ABCD]+)", body or ""):
        nums, ans = m.group(1), m.group(2)
        seq = _restore_numbers(nums)
        if seq is None or len(seq) != len(ans):
            continue
        # 合理性约束（这两个检查是"不猜"的兜底）：
        # ① 回溯对**任何**数字串都能成功（"一个数吃掉全部数字"即可），所以还要看它像不像题号；
        # ② 题号不可能超过 99；单个题号不可能对应多个答案。
        if max(seq) > 99 or (len(seq) == 1 and len(ans) > 1):
            continue
        for n, a in zip(seq, ans):
            out.setdefault(n, a)
    return out


def _restore_numbers(digits: str) -> list[int] | None:
    """`12345678` → [1..8]；`910111213141516` → [9..16]。

    ⚠️ **必须变长解析**：序号串会跨位数（`9,10,11…`）。第一版用**固定宽度**，
    于是 `910111213141516` 在宽度 1 处遇进位失败、在宽度 2 处被解成 `91,01,…` ——
    实测 391 道题里只取回 154 个答案（39%），就是这个 bug 造成的。
    现在改为**回溯**：每一位试 1~3 位宽，要求与上一个数连续，且**恰好用完**所有数字。

    数字被 OCR 吃掉或多余时返回 None —— **不猜**：猜出来的序列会让整段答案错位，
    比"少要这几个答案"糟得多。
    """
    if not digits or not digits.isdigit():
        return None
    n = len(digits)

    def _walk(i: int, expect: int | None, acc: list[int]) -> list[int] | None:
        if i == n:
            return list(acc)
        for w in (1, 2, 3):
            if i + w > n:
                break
            piece = digits[i : i + w]
            if w > 1 and piece[0] == "0":  # 前导零不是我们要的编号写法
                continue
            val = int(piece)
            if expect is not None and val != expect:
                continue
            acc.append(val)
            # 约束**必须立刻生效**（从第二个数起就要求 == 上一个+1）。
            # 第一版写成"expect 为 None 时继续传 None"，等于放开了第二个数，
            # 于是 `1718192021222324` 被解成 [1,7,…] —— 反而比原来的固定宽度更糟。
            got = _walk(i + w, val + 1, acc)
            acc.pop()
            if got is not None:
                return got
        return None

    return _walk(0, None, [])


#: 答案正文里每题的形态：`1.正确答案是：B … 西米学府团队易错选项提醒：A`
_CORRECT_RE = re.compile(r"(\d{1,2})[．.]\s*正确答案是[：: ]*([ABCD])")
_TRAP_RE = re.compile(r"易错选项提醒[：: ]*([ABCD])")


def parse_trap_hints(body: str) -> dict[int, str]:
    """答案正文里每题的「易错选项提醒」→ `{题号: 易错选项}`。

    ## 为什么值得单独抽出来

    那是教辅**明确指出的"最容易选错的那个选项"** —— 两个选项都说得通，才会有人选错。
    它天然就是**歧义候选**，而且比我们自己去构造（§5 第 12 项里 N4 那条路失败了）
    更接近真实考题：这是**机构自己承认**的易混点，不是我们猜的。

    ⚠️ **"易错"不等于"两个都对"** —— 它可能只是"干扰项设计得巧妙"。
    是否构成真歧义，仍需人工或模型裁定。**本函数只负责把候选挑出来，不下结论。**
    """
    out: dict[int, str] = {}
    for m in _CORRECT_RE.finditer(body or ""):
        start = m.end()
        nxt = _CORRECT_RE.search(body, start)
        seg = body[start : nxt.start() if nxt else len(body)]
        t = _TRAP_RE.search(seg)
        if t:
            out.setdefault(int(m.group(1)), t.group(1))
    return out


def build_dataset(q_text: str, a_text: str, *, stage: str = "中学", subject: str = "综合素质") -> dict:
    """题面 + 答案 → 评测集 dict（含统计与丢弃原因）。"""
    exams_q = split_exams(q_text)
    exams_a = split_exams(a_text)
    stats = {
        "n_exams_q": len(exams_q),
        "n_exams_a": len(exams_a),
        "n_items": 0,
        "n_with_trap": 0,  # 带有「易错选项提醒」的题数 = 歧义候选数
        "dropped": {"exam_misaligned": 0, "no_answer": 0, "bad_question": 0},
        "per_exam": [],
    }

    items: list[dict] = []
    pair_n = min(len(exams_q), len(exams_a))
    if len(exams_q) != len(exams_a):
        stats["dropped"]["exam_misaligned"] += abs(len(exams_q) - len(exams_a))

    for k in range(pair_n):
        eq, ea = exams_q[k], exams_a[k]
        # 对齐校验：年份/上下半年必须一致，否则说明两边分段错位 → 这一对整体不要
        if (eq["year"], eq["half"]) != (ea["year"], ea["half"]):
            stats["dropped"]["exam_misaligned"] += 1
            continue
        qs = parse_questions(eq["body"])
        ans = parse_answer_sheet(ea["body"])
        traps = parse_trap_hints(ea["body"])
        kept = 0
        for no, q in sorted(qs.items()):
            a = ans.get(no)
            if a is None:
                stats["dropped"]["no_answer"] += 1
                continue
            if a not in _KEYS:
                stats["dropped"]["bad_question"] += 1
                continue
            # 易错项若是正确答案本身，说明 OCR 或原书标错 —— 不采信
            trap = traps.get(no)
            if trap is not None and trap == a:
                trap = None
            if trap is not None:
                stats["n_with_trap"] += 1
            items.append({
                "id": f"{eq['year']}{eq['half']}-{no}",
                "type": "single",
                "year": eq["year"],
                "half": eq["half"],
                "stage": stage,
                "subject": subject,
                "no": no,
                "stem": q["stem"],
                "options": q["options"],
                "answer": [a],
                "trap": trap,  # 教辅标注的易错项 = 歧义候选（**是否真歧义待裁定**）
                "source_page": q.get("page"),
            })
            kept += 1
        stats["per_exam"].append({"year": eq["year"], "half": eq["half"], "n_questions": len(qs),
                                  "n_answers": len(ans), "kept": kept})
    stats["n_items"] = len(items)
    return {
        "note": "教辅整理的扫描版真题经 OCR 结构化；**半官方**（非官方发布）。仅作评测集，不入库。",
        "authority": "半官方",
        "source": {
            "origin": "教辅（西米学府团队）整理，非教育部教育考试院发布",
            "ocr": "rapidocr-onnxruntime（有非零错误率，故每题均过校验；不合格项丢弃而不位移）",
            "answer_from": "答案速查表（不依赖正文 OCR）",
        },
        "stats": stats,
        "items": items,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions")
    ap.add_argument("answers")
    ap.add_argument("out")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    q_text = Path(args.questions).read_text(encoding="utf-8")
    a_text = Path(args.answers).read_text(encoding="utf-8")
    data = build_dataset(q_text, a_text)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    s = data["stats"]
    print(f"试卷 题面 {s['n_exams_q']} / 答案 {s['n_exams_a']}　对齐后保留 {len(s['per_exam'])} 份")
    print(f"题目 {s['n_items']} 道　丢弃：{s['dropped']}")
    if args.report:
        lines = [
            "# 真题单选集（OCR 结构化）",
            "",
            f"- 试卷：题面 {s['n_exams_q']} 份 / 答案 {s['n_exams_a']} 份",
            f"- **题目 {s['n_items']} 道**；丢弃 {s['dropped']}",
            "- 权威级别：**半官方**（教辅整理，非官方发布）",
            "",
            "| 年份 | 上下 | 解析出题目 | 解析出答案 | 保留 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for e in s["per_exam"]:
            lines.append(f"| {e['year']} | {e['half']} | {e['n_questions']} | {e['n_answers']} | {e['kept']} |")
        lines += ["", "⚠️ OCR 有非零错误率：答案取自速查表（比正文可靠），但**仍需抽样核对**。"]
        Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"报告：{args.report}")
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
