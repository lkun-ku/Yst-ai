"""`scripts/parse_choice_ocr.py` 的关键解析逻辑测试（不联网、不依赖 OCR）。

钉住的是三件最容易出错、且**错了会导致整批数据错位**的事：
① 试卷分段与对齐；② 速查表的**变长**序号还原；③ 选项切分与"丢弃而不位移"。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.parse_choice_ocr import (  # noqa: E402
    _restore_numbers,
    build_dataset,
    parse_answer_sheet,
    parse_questions,
    parse_trap_hints,
    split_exams,
)


# ---------------- 试卷分段 ----------------

def test_按试卷头切分并保留年份与上下半年():
    text = (
        "封面内容\n"
        "===== page 1 =====\n2025年上半年&教师资格证考试笔试真题中学&《综合素质》\n题目甲\n"
        "===== page 2 =====\n2024年下半年&教师资格证考试笔试真题中学&《综合素质》\n题目乙\n"
    )
    exams = split_exams(text)
    assert [(e["year"], e["half"]) for e in exams] == [(2025, "上"), (2024, "下")]
    assert "题目甲" in exams[0]["body"] and "题目乙" in exams[1]["body"]


def test_头部之前的封面内容不计入任何试卷():
    assert "封面" not in "".join(e["body"] for e in split_exams("封面\n2025年上半年&…《综合素质》正文"))


# ---------------- 速查表：变长序号 ----------------

def test_单位数序号():
    assert _restore_numbers("12345678") == [1, 2, 3, 4, 5, 6, 7, 8]


def test_跨位数的序号_这是第一版踩的坑():
    """`910111213141516` = 9..16（**不是** 91,01,…）。固定宽度解不出来，必须变长 + 回溯。"""
    assert _restore_numbers("910111213141516") == [9, 10, 11, 12, 13, 14, 15, 16]
    assert _restore_numbers("2526272829") == [25, 26, 27, 28, 29]


def test_形如_17181920_的段落():
    assert _restore_numbers("1718192021222324") == [17, 18, 19, 20, 21, 22, 23, 24]


def test_连续序列按变长还原():
    """`91011121314` = 9..14（完整且连续）。

    ⚠️ 这条起初被我写成"应返回 None"—— **是断言写错**：它其实是完整的 9,10,11,12,13,14。
    """
    assert _restore_numbers("91011121314") == [9, 10, 11, 12, 13, 14]


def test_回溯总能找到解_所以合理性检查必须在调用方():
    """单个数吃掉全部数字，对**任何**数字串都能成功 —— 于是 `_restore_numbers` 几乎不返回 None。

    这不是 bug，但意味着"不猜"的保证**不能靠它**：必须由 `parse_answer_sheet` 的
    长度匹配 + 题号上限来兜底。否则 `序号135答案A` 会被当成"第 135 题"。
    """
    assert _restore_numbers("135") == [135]
    assert parse_answer_sheet("序号135答案A") == {}   # 题号不可能到 135
    assert parse_answer_sheet("序号1234答案ABC") == {}  # 序号 4 个、答案 3 个 → 不配对


def test_速查表成对解析():
    body = "序号12345678答案CBDBDDCA序号910111213141516答案BAABCCDA"
    assert parse_answer_sheet(body) == {
        1: "C", 2: "B", 3: "D", 4: "B", 5: "D", 6: "D", 7: "C", 8: "A",
        9: "B", 10: "A", 11: "A", 12: "B", 13: "C", 14: "C", 15: "D", 16: "A",
    }


def test_序号与答案长度不等时整段丢弃():
    """长度不等说明 OCR 吃错了字；一旦强行配对，后面全部错位。"""
    assert parse_answer_sheet("序号123答案ABCD") == {}


# ---------------- 题面：选项与题号 ----------------

def test_解析题干与四个选项():
    body = (
        "===== page 2 =====\n"
        "1．陶老师认为教材是个例子，要靠教师运用。（）A.②③B.②③④C.①④D.①②③④\n"
        "2．某地展示了三节课。（）A.甲B.乙C.丙D.丁\n"
    )
    qs = parse_questions(body)
    assert sorted(qs) == [1, 2]
    assert qs[1]["stem"].startswith("陶老师")
    assert [o["key"] for o in qs[1]["options"]] == ["A", "B", "C", "D"]
    assert qs[1]["options"][1]["text"] == "②③④"
    assert qs[1]["page"] == 2  # 保留页码，出错能回原页核对


def test_选项数不为4的题目被丢弃():
    """只有 3 个选项 → 多半是 OCR 漏了一行；丢掉它（**且不位移其它题**）。"""
    qs = parse_questions("===== page 1 =====\n5．题干内容足够长的一句话。（）A.甲B.乙C.丙")
    assert qs == {}


def test_题干过短被丢弃():
    qs = parse_questions("===== page 1 =====\n6．短。（）A.甲B.乙C.丙D.丁")
    assert qs == {}


def test_题号对齐_漏号不会导致位移():
    """OCR 漏掉第 2 题时，第 3 题仍应挂在 3 上 —— 与答案的题号对齐才成立。"""
    body = (
        "===== page 1 =====\n"
        "1．第一题的题干足够长一些。（）A.甲B.乙C.丙D.丁\n"
        "3．第三题的题干足够长一些。（）A.甲B.乙C.丙D.丁\n"
    )
    qs = parse_questions(body)
    assert sorted(qs) == [1, 3]


# ---------------- 合并 ----------------

def test_合并时年份对不上则整份试卷丢弃():
    q = "2025年上半年&真题《综合素质》\n===== page 1 =====\n1．题干足够长的一句话在这。（）A.甲B.乙C.丙D.丁"
    a = "2024年下半年&真题《综合素质》\n答案速查表序号1答案A"
    data = build_dataset(q, a)
    assert data["stats"]["n_items"] == 0
    assert data["stats"]["dropped"]["exam_misaligned"] == 1


def test_合并成功并带上权威级别():
    q = "2025年上半年&真题《综合素质》\n===== page 1 =====\n1．题干足够长的一句话在这。（）A.甲B.乙C.丙D.丁"
    a = "2025年上半年&真题《综合素质》\n答案速查表序号1答案B"
    d = build_dataset(q, a)
    assert d["stats"]["n_items"] == 1
    it = d["items"][0]
    assert it["answer"] == ["B"] and it["year"] == 2025 and it["id"] == "2025上-1"
    assert d["authority"] == "半官方"


def test_抽取教辅标注的易错项():
    """「易错选项提醒：A」是机构自己承认的易混点 —— 天然就是歧义候选（不待我们构造）。"""
    body = (
        "1.正确答案是：B解析：……。西米学府团队易错选项提醒：A"
        "2.正确答案是：C解析：……。西米学府团队易错选项提醒：D"
    )
    assert parse_trap_hints(body) == {1: "A", 2: "D"}


def test_易错项等于正确答案时不采信():
    """OCR 错字或原书标错都可能造成这种自相矛盾 —— 宁可丢掉这个候选。"""
    body = "1.正确答案是：B解析：……易错选项提醒：B"
    assert parse_trap_hints(body) == {1: "B"}  # 抽取层如实给出
    d = build_dataset(
        "2025年上半年&真题《综合素质》\n===== page 1 =====\n1．题干足够长的一句话在这。（）A.甲B.乙C.丙D.丁",
        "2025年上半年&真题《综合素质》\n答案速查表序号1答案B\n1.正确答案是：B解析：……易错选项提醒：B",
    )
    assert d["items"][0]["trap"] is None  # 合并层判定不采信
    assert d["stats"]["n_with_trap"] == 0


def test_没有答案的题目被丢弃而不是硬凑():
    q = (
        "2025年上半年&真题《综合素质》\n===== page 1 =====\n"
        "1．题干足够长的一句话在这。（）A.甲B.乙C.丙D.丁\n"
        "2．题干足够长的一句话在这。（）A.甲B.乙C.丙D.丁"
    )
    a = "2025年上半年&真题《综合素质》\n答案速查表序号1答案A"
    d = build_dataset(q, a)
    assert d["stats"]["n_items"] == 1
    assert d["stats"]["dropped"]["no_answer"] == 1
