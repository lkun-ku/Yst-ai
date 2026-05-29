"""把 OCR 出来的**主观题**（题面 + 答案）结构化成「满分答卷库」条目。

## 形态（实测）

- **题面**：每份试卷有若干主观题，题干含**材料原文**（长段落），题号如 `30.`；
- **答案**：`30.正确答案是：<示范作答>` 后接 `解析：…`，有时带 `【特殊说明】`
  （例如"主观题的答案并不唯一，考生也可以从其他角度来分析材料"）与`考点索引`。

## 三条边界（同选择题那套）

1. **权威级别 = 半官方**：教辅示范答案，不是官方评分细则（官方不公开）；
2. **只存答题/批改必需字段**：题干 + 示范作答 + 采分点，不复制整篇解析；
3. **不进知识库**：产物落 `eval/datasets/真题主观/`，再由 `build_answer_bank.py` 汇入答案库。

## 采分点从哪来

示范作答本身是一段完整文字，**采分点用 `marking.split_reference_points()` 切**，
并**保留原文**（`reference`）—— 切分是启发式的，人工核对时以原文为准。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.parse_choice_ocr import split_exams  # noqa: E402

#: `30.正确答案是：……` —— 到「解析」「考点索引」「下一题号」为止
_ANSWER_RE = re.compile(r"(\d{1,2})[．.]\s*正确答案是[：: ]*", re.M)
_STOP_RE = re.compile(r"解析[：:]|考点索引|【特殊说明】|西米学府")
#: 题面里的主观题号。
#:
#: ⚠️ **不能锚行首**：实测题号是**行内**出现的 ——
#: `…评析王老师的教育行为。32.材料以自己的感受力尽可能多地从书中获取印象…`
#: 第一版写了 `(?m)^\s*` 要求行首，于是 22 份试卷**一道都解析不出来**。
#: 现在改为任意位置匹配，靠 `(?<!\d)` 挡住长数字的尾巴（如 `2025.` 不会被当成 `5.`），
#: 再限定题号范围；**最终由"答案侧能配对"兜底**（配不上的题自然被丢弃）。
_QNO_RE = re.compile(r"(?<!\d)(\d{1,2})[．.]")

#: 科目一主观题号：材料分析 30–32 + 写作 33（留一点余量）
_MIN_NO, _MAX_NO = 30, 35


def _pages(body: str) -> list[tuple[int, str]]:
    chunks = re.split(r"^===== page (\d+) =====$", body or "", flags=re.MULTILINE)
    return [
        (int(chunks[i]), chunks[i + 1] if i + 1 < len(chunks) else "")
        for i in range(1, len(chunks), 2)
    ]


def parse_answers(body: str, min_no: int = 25) -> dict[int, dict]:
    """答案正文 → `{题号: {reference, page, special_note}}`。只取 `min_no` 以上的题号。"""
    out: dict[int, dict] = {}
    for page_no, text in _pages(body):
        marks = list(_ANSWER_RE.finditer(text))
        for idx, m in enumerate(marks):
            no = int(m.group(1))
            if no < min_no or no in out:
                continue
            start = m.end()
            end = len(text)
            for cand in (_ANSWER_RE.search(text, start), _STOP_RE.search(text, start)):
                if cand:
                    end = min(end, cand.start())
            seg = text[start:end].strip()
            if len(seg) < 20:
                continue
            # 「答案并不唯一」这类特殊说明，是**歧义线索**，值得留档。
            # ⚠️ 它在原文里位于**示范作答之后、`解析：`之前** —— 也就是在 seg **里**，
            # 不在 seg 之后。第一版搜的是 `text[end:...]`（stop 之后），于是全都没抽到。
            note = ""
            note_m = re.search(r"【特殊说明】[^\n]{0,200}", seg) or re.search(
                r"【特殊说明】[^\n]{0,200}", text[start : start + 2000]
            )
            if note_m:
                note = note_m.group(0)
            out[no] = {"reference": seg, "page": page_no, "special_note": note}
    return out


def parse_questions(body: str, min_no: int = _MIN_NO) -> dict[int, dict]:
    """题面 → `{题号: {stem, page}}`（题干含材料原文，故不按行切，按题号切）。

    题号范围限定在 `[_MIN_NO, _MAX_NO]`：材料正文里难免有别的小数字带点号，
    但**只有能跟答案侧配对的题号才会活下来**，所以范围 + 配对是双重保险。
    """
    out: dict[int, dict] = {}
    for page_no, text in _pages(body):
        marks = [
            m for m in _QNO_RE.finditer(text) if _MIN_NO <= int(m.group(1)) <= _MAX_NO
        ]
        for idx, m in enumerate(marks):
            start = m.end()
            end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
            seg = text[start:end].strip()
            no = int(m.group(1))
            if len(seg) < 20 or no in out:
                continue
            out[no] = {"stem": seg, "page": page_no}
    return out


def build(q_text: str, a_text: str, *, stage: str = "中学", subject: str = "综合素质") -> dict:
    from app.services.marking import split_reference_points

    eq, ea = split_exams(q_text), split_exams(a_text)
    stats = {"n_exams": 0, "n_items": 0, "n_with_note": 0, "n_points": 0}
    items: list[dict] = []
    for k in range(min(len(eq), len(ea))):
        if (eq[k]["year"], eq[k]["half"]) != (ea[k]["year"], ea[k]["half"]):
            continue
        stats["n_exams"] += 1
        qs = parse_questions(eq[k]["body"])
        ans = parse_answers(ea[k]["body"])
        for no, a in sorted(ans.items()):
            q = qs.get(no)
            if q is None:
                continue
            points = split_reference_points(a["reference"])
            if a.get("special_note"):
                stats["n_with_note"] += 1
            stats["n_points"] += len(points)
            items.append({
                "id": f"{eq[k]['year']}{eq[k]['half']}-{no}",
                "qtype": "material" if no <= 32 else "writing" if no >= 33 else "short",
                "stage": stage,
                "subject": subject,
                "year": eq[k]["year"],
                "half": eq[k]["half"],
                "no": no,
                "stem": q["stem"],
                "reference": a["reference"],   # 示范作答 = 满分卷
                "points": points,              # 由 split_reference_points 切出（启发式）
                "special_note": a.get("special_note", ""),
                "source_page": a["page"],
            })
    stats["n_items"] = len(items)
    return {
        "note": "教辅整理的历年主观题与**示范作答**（半官方）；采分点由启发式切分，人工核对应以 reference 原文为准。",
        "authority": "半官方",
        "stats": stats,
        "items": items,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions")
    ap.add_argument("answers")
    ap.add_argument("out")
    args = ap.parse_args()
    data = build(
        pathlib.Path(args.questions).read_text(encoding="utf-8"),
        pathlib.Path(args.answers).read_text(encoding="utf-8"),
    )
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    s = data["stats"]
    print(f"试卷 {s['n_exams']} 份 · 题目 {s['n_items']} 道 · 采分点合计 {s['n_points']} · "
          f"带『答案不唯一』说明的 {s['n_with_note']} 道")
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
