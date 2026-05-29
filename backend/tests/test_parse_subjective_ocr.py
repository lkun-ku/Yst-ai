"""主观题解析的回归测试 —— 钉住两条曾导致**大面积丢题**的教训。

1. **跨页**：题干与答案都会跨页，按页切开再匹配会把跨页那条截断（实测每卷只出 1~2 道）；
2. **按偏移归属**：题属于哪一份卷，由**位置**决定，而不是由"切出来的一段文本"决定 ——
   第一版按试卷切分后逐卷匹配，一旦试卷边界判错（页眉在页中重复），边界另一侧的题整条丢失。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.parse_subjective_ocr import (  # noqa: E402
    build,
    parse_answers_global,
    parse_questions_global,
)

_HDR = "2024年下半年&教师资格证考试笔试真题中学&《综合素质》"
_HDR2 = "2024年上半年&教师资格证考试笔试真题中学&《综合素质》"

#: 一道题**横跨两页**：题号在第 1 页末，正文在第 2 页 —— 第一版就是在这里丢的。
_Q_CROSS = (
    f"{_HDR}\n"
    "===== page 1 =====\n"
    "得分评卷人\n30."
    "===== page 2 =====\n"
    "材料：某中学王老师在课堂上公开批评学生，并让学生站到教室后面。\n"
    "问题：请从教师职业道德的角度评析王老师的行为。（14分）\n"
    "31.材料：李老师坚持每天提前到校，认真备课，关心每一位学生。\n"
    "问题：请从教师职业道德的角度评析李老师的教育行为。（14分）\n"
    "32.阅读下面的材料，回答下面的问题。（14分）\n"
    "材料一：一般来说，桃子适合在海拔五百米以下种植。\n"
    "材料二：不同的作物对土壤与气候的要求并不相同。\n"
    # ⚠️ 题干必须**足够长**（>20 字）才不会被"过短即噪声"的护栏丢弃 ——
    # 真实题干含材料原文，天然很长；合成样本若写太短，测的就不是解析逻辑而是那道护栏。
    "33.阅读下面的材料，根据要求作文。（50分）\n"
    "要求：自拟标题，自选角度，不少于八百字，文体不限，诗歌除外。\n"
)

_A_CROSS = (
    f"{_HDR}\n"
    "===== page 1 =====\n"
    "30．正确答案是："
    "===== page 2 =====\n"
    "王老师的行为违反了关爱学生的要求：应当尊重学生人格，不讽刺、不体罚。\n"
    "31．正确答案是：李老师的做法体现了爱岗敬业：忠诚于人民教育事业，勤恳敬业。\n"
    "32．正确答案是：（1）材料一说明要因材施教；（2）材料二说明要循序渐进。\n"
    "33．正确答案是：【立意】围绕因材施教展开，结构清晰，语言流畅。\n"
)


def test_跨页的题号不会因为按页切分而丢失():
    """第一版按页匹配：`30.` 在第 1 页、正文在第 2 页 → 该条被截断丢弃。"""
    qs = {(x["no"]) for x in parse_questions_global(_Q_CROSS)}
    assert 30 in qs, f"跨页的第 30 题丢了（实得 {sorted(qs)}）"
    assert {30, 31, 32, 33} <= qs


def test_跨页的答案不会丢失():
    ans = {x["no"] for x in parse_answers_global(_A_CROSS)}
    assert {30, 31, 32, 33} <= ans, f"跨页答案丢失（实得 {sorted(ans)}）"


def test_答案段落不吞掉下一份试卷的卷头():
    """段落终点必须考虑**下一份卷头**，否则会把下一卷的页眉并进本题的示范作答。"""
    two = _A_CROSS + "===== page 3 =====\n" + _HDR2 + "\n30．正确答案是：第二卷的示范作答内容在这里，足够长。\n"
    items = parse_answers_global(two)
    first = [x for x in items if x["year"] == 2024 and x["half"] == "下" and x["no"] == 30][0]
    assert "上半年" not in first["reference"]
    assert "第二卷" not in first["reference"]


def test_同一卷同一题号只取第一次():
    """解析正文里会出现对题号的回指 —— 不能因此产生重复条目。"""
    dup = (
        f"{_HDR}\n30．正确答案是：这是真正的示范作答，长度足够超过二十个字符。\n"
        "30．正确答案是：这是解析里的回指，不应覆盖前面那条。\n"
    )
    items = parse_answers_global(dup)
    assert len([x for x in items if x["no"] == 30]) == 1


def test_带特殊说明的题被记下来():
    """「答案并不唯一」是**歧义线索**，必须落档。

    ⚠️ 它位于示范作答之后、`解析：`之前 —— 而 `【特殊说明】` 本身就在停止词表里，
    所以只在截断后的段落里找**永远找不到**（这正是"带特殊说明 0"的原因）。
    """
    text = (
        f"{_HDR}\n30．正确答案是：示范作答内容足够长的一段文字，用于满足长度门槛。\n"
        "【特殊说明】主观题的答案并不唯一，考生也可以从其他角度来分析材料。\n"
        "解析：同上。\n"
    )
    items = parse_answers_global(text)
    assert items and "【特殊说明】" in items[0]["special_note"]
    assert "答案并不唯一" in items[0]["special_note"]


def test_按三元组合并_不做序号对齐():
    """题面与答案各 4 题 → 合并出 4 条，且 identity 是 `(年份, 上下, 题号)`。"""
    data = build(_Q_CROSS, _A_CROSS)
    assert data["stats"]["n_items"] == 4
    ids = sorted(it["id"] for it in data["items"])
    assert ids == ["2024下-30", "2024下-31", "2024下-32", "2024下-33"]


def test_只有答案没有题面时如实计数而不是硬凑():
    data = build(f"{_HDR}\n===== page 1 =====\n", _A_CROSS)
    assert data["stats"]["n_items"] == 0
    assert data["stats"]["n_stem_missing"] == 4


def test_答案过短被丢弃_不把噪声当示范作答():
    """`正确答案是：` 后面紧跟段落终点（如 `解析：`）时，抽出来的不足 20 字 → 丢弃。"""
    text = f"{_HDR}\n30．正确答案是：解析：同上。\n31．正确答案是：这一段足够长，应当被保留下来作为示范作答。\n"
    nos = {x["no"] for x in parse_answers_global(text)}
    assert 30 not in nos and 31 in nos
