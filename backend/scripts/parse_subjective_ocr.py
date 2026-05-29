"""把 OCR 出来的**主观题**（题面 + 答案）结构化成「满分答卷库」条目。

## 形态（实测）

- **题面**：每份试卷 4 道主观题（30/31/32 材料分析 + 33 写作），题干含**材料原文**（长段落）；
- **答案**：`30．正确答案是：<示范作答>` 后接 `解析：…`，有时带 `【特殊说明】` 与 `考点索引`。

## 核心设计：**按偏移归属，不按切分归属**

第一版是"先按试卷切分、再逐卷匹配"，实测每卷只出 1~2 道（缺 30、31）。逐层量化发现：
`_QNO_RE` 在**整份文件**里能命中它们，但在"**切出来的那一卷**"里命中不到 —— 也就是
**试卷边界判错了**（页眉在页中等重复出现时，边界会落在题的中间），边界另一侧的题整条丢失。

所以本版改为：整份文件拼成连续文本，题号/答案按**位置**决定属于哪一份卷
（`_exam_spans` 给出各卷的字符区间）。这样对边界不敏感 —— 题属于谁，由位置决定，
而不是由"切出来的一段文本"决定。

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
import bisect
import json
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 试卷头（与选择题解析器同一口径）。捕获年份与上下半年。
_HDR_RE = re.compile(r"(\d{4})年([上下])半年[^《]{0,60}《综合素质》")

#: `30．正确答案是：……`
_ANSWER_RE = re.compile(r"(\d{1,2})[．.]\s*正确答案是[：: ]*")
_STOP_RE = re.compile(r"解析[：:]|考点索引|【特殊说明】|西米学府")

#: 题面里的主观题号。
#:
#: ⚠️ **不能锚行首**：实测题号是**行内**出现的 ——
#: `…评析王老师的教育行为。32.材料以自己的感受力尽可能多地从书中获取印象…`
#: 也不能按页/按卷切后再匹配（见模块 docstring）。
#: `(?<!\d)` 挡住长数字的尾巴（如 `2025.` 不会被当成 `5.`）；范围限定挡住材料里的杂数。
_QNO_RE = re.compile(r"(?<!\d)(\d{1,2})[．.]")

#: 科目一主观题号：材料分析 30–32 + 写作 33（留一点余量）
_MIN_NO, _MAX_NO = 30, 35


def _flat(text: str) -> tuple[str, list[int]]:
    """整份文件 → `(连续文本, 每字符所属页码)`。

    去掉 `===== page N =====` 标记行本身，但**记录页码**：题干与答案都会跨页，
    按页切开再匹配会把跨页的那条截断（这正是第一版丢题的原因之一）。
    """
    parts = re.split(r"^===== page (\d+) =====$", text or "", flags=re.MULTILINE)
    # ⚠️ **第一个页标记之前的文字不能丢**：多份资料把**卷头**放在那里（OCR 的首段），
    # 丢了它就找不到任何试卷区间，于是**所有条目都会被跳过**（实测：整份解析返回空列表）。
    # 那部分文字归属"第 0 页"（未分页的卷首）。
    flat = parts[0] if parts else ""
    page_of: list[int] = [0] * len(flat)
    for i in range(1, len(parts), 2):
        page_no = int(parts[i])
        seg = parts[i + 1] if i + 1 < len(parts) else ""
        flat += seg
        page_of.extend([page_no] * len(seg))
    return flat, page_of


def _exam_spans(flat: str) -> tuple[list[int], list[tuple[int, str]]]:
    """各卷的起始偏移 → `([start...], [(year, half)...])`（供 `bisect` 定位用）。"""
    starts: list[int] = []
    metas: list[tuple[int, str]] = []
    for m in _HDR_RE.finditer(flat):
        starts.append(m.start())
        metas.append((int(m.group(1)), m.group(2)))
    return starts, metas


def _owner(offset: int, starts: list[int], metas: list[tuple[int, str]]) -> tuple[int, str] | None:
    """某个字符位置属于哪一份卷 —— 二分找最后一个不晚于它的卷头。"""
    if not starts:
        return None
    i = bisect.bisect_right(starts, offset) - 1
    return metas[i] if i >= 0 else None


def parse_answers_global(text: str) -> list[dict]:
    """整份答案文件 → 条目列表（每条带归属的 `(year, half)` 与页码）。"""
    flat, page_of = _flat(text)
    starts, metas = _exam_spans(flat)
    marks = list(_ANSWER_RE.finditer(flat))
    out: list[dict] = []
    seen: set[tuple[int, str, int]] = set()
    for idx, m in enumerate(marks):
        no = int(m.group(1))
        if not (_MIN_NO <= no <= _MAX_NO):
            continue
        owner = _owner(m.start(), starts, metas)
        if owner is None:
            continue
        # 段落终点：下一个答案标记 / 停止词 / **下一份卷头**（否则会把下卷的页眉吞进来）
        end = len(flat)
        stops = [
            _ANSWER_RE.search(flat, m.end()),
            _STOP_RE.search(flat, m.end()),
            _HDR_RE.search(flat, m.end()),
        ]
        for cand in stops:
            if cand:
                end = min(end, cand.start())
        seg = flat[m.end() : end].strip()
        if len(seg) < 20:
            continue
        key = (owner[0], owner[1], no)
        if key in seen:
            continue  # 同一卷同一题号只取第一次（后出现的是解析里的回指）
        seen.add(key)
        note = ""
        # ⚠️ 注记恰好在**段落终点之后**：`_STOP_RE` 里就包含 `【特殊说明】`，
        # 所以 `seg` 会在它前面截断 —— 只在 seg 里找永远找不到（这就是"带特殊说明 0"的原因）。
        note_m = re.search(r"【特殊说明】[^\n]{0,200}", seg) or re.search(
            r"【特殊说明】[^\n]{0,200}", flat[end : end + 400]
        )
        if note_m:
            note = note_m.group(0)
        out.append(
            {
                "year": owner[0],
                "half": owner[1],
                "no": no,
                "reference": seg,
                "special_note": note,
                "page": page_of[m.start()] if m.start() < len(page_of) else 0,
            }
        )
    return out


def parse_questions_global(text: str) -> list[dict]:
    """整份题面文件 → 条目列表（题干含材料原文，故按题号切到**下一个题号**为止）。"""
    flat, page_of = _flat(text)
    starts, metas = _exam_spans(flat)
    marks = [m for m in _QNO_RE.finditer(flat) if _MIN_NO <= int(m.group(1)) <= _MAX_NO]
    out: list[dict] = []
    seen: set[tuple[int, str, int]] = set()
    for idx, m in enumerate(marks):
        no = int(m.group(1))
        owner = _owner(m.start(), starts, metas)
        if owner is None:
            continue
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(flat)
        # 也不许跨过下一份卷头
        hdr = _HDR_RE.search(flat, m.end())
        if hdr:
            end = min(end, hdr.start())
        seg = flat[m.end() : end].strip()
        if len(seg) < 20:
            continue
        key = (owner[0], owner[1], no)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "year": owner[0],
                "half": owner[1],
                "no": no,
                "stem": seg,
                "page": page_of[m.start()] if m.start() < len(page_of) else 0,
            }
        )
    return out


def build(q_text: str, a_text: str, *, stage: str = "中学", subject: str = "综合素质") -> dict:
    """题面 + 答案 → 满分答卷库条目（**按 `(年份, 上下, 题号)` 合并**，不做序号对齐）。

    为什么按这个三元组而不是"第 k 卷对第 k 卷"：序号对齐要求两侧切分完全一致，
    而这正是第一版崩掉的地方。三元组是**内容身份**，一侧切错也不会连带错。
    """
    from app.services.marking import split_reference_points

    qs = {(x["year"], x["half"], x["no"]): x for x in parse_questions_global(q_text)}
    ans = parse_answers_global(a_text)

    stats = {"n_exams": len(_exam_spans(_flat(q_text)[0])[1]), "n_items": 0,
             "n_with_note": 0, "n_points": 0, "n_stem_missing": 0}
    items: list[dict] = []
    for a in ans:
        key = (a["year"], a["half"], a["no"])
        q = qs.get(key)
        if q is None:
            stats["n_stem_missing"] += 1
            continue
        points = split_reference_points(a["reference"])
        if a.get("special_note"):
            stats["n_with_note"] += 1
        stats["n_points"] += len(points)
        items.append(
            {
                "id": f"{a['year']}{a['half']}-{a['no']}",
                "qtype": "writing" if a["no"] >= 33 else "material",
                "stage": stage,
                "subject": subject,
                "year": a["year"],
                "half": a["half"],
                "no": a["no"],
                "stem": q["stem"],
                "reference": a["reference"],   # 示范作答 = 满分卷
                "points": points,              # 由 split_reference_points 切出（启发式）
                "special_note": a.get("special_note", ""),
                "source_page": a["page"],
            }
        )
    stats["n_items"] = len(items)
    return {
        "note": (
            "教辅整理的历年主观题与**示范作答**（半官方）；采分点由启发式切分，"
            "人工核对应以 reference 原文为准。"
        ),
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
    print(
        f"试卷 {s['n_exams']} 份 · 题目 {s['n_items']} 道 · 采分点合计 {s['n_points']} · "
        f"带特殊说明 {s['n_with_note']} · 有答案但缺题面的 {s['n_stem_missing']}"
    )
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
