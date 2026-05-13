"""考纲语料：切片规则 + 逐字保真。

**为什么单列一个文件**：考纲是本项目里**唯一一类"结构由官方文档决定、而我们无法控制"**的语料
—— 官方 .doc 的标题层级、序号写法（`⒈` 与 `2.` 混用）、空白字符（全角空格 U+3000）都不规范。
切片规则必须能在这种输入上稳定工作，而"逐字"这条底线必须有断言守着：
语料一旦被改动（哪怕只改了看不见的空白），测试要立刻红，而不是让引用校验悄悄失效。
"""
import re
from pathlib import Path

from app.services.kb_corpus import plan_file, split_syllabus

SYLLABUS_DIR = Path(__file__).resolve().parents[1] / "data" / "official" / "syllabus"

#: 官方 .doc 的转换产物**随仓库留档**（`data/official/_source/`）——
#: 这样"逐字保真"是**持续被 CI 执行**的断言，而不是抓取当天跑一次的一次性检查。
#: 二进制 .doc 不留（43KB/份，可用 frontmatter 里的 source_file URL 重新取）。
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "official" / "_source"

_REAL_FILES = [
    ("综合素质-101-幼儿园.md", "101_综合素质_幼儿园.txt"),
    ("综合素质-201-小学.md", "201_综合素质_小学.txt"),
    ("综合素质-301-中学.md", "301_综合素质_中学.txt"),
]


# ---------------- 切片规则 ----------------

_BODY = """# 文档标题

## 一、考试目标

这是目标段。

## 二、内容模块

### （一）职业教育

#### 1.教育观

要求甲。

要求乙。

#### 2.学生观

要求丙。

### （二）没有子标题的模块

要求丁。
"""


def test_叶子标题各成一片且保留标题文字():
    plans = split_syllabus("测试考纲", _BODY)
    headings = [p.heading_path for p in plans]
    assert headings == [
        "测试考纲 / 一、考试目标",
        "测试考纲 / （一）职业教育 / 1.教育观",
        "测试考纲 / （一）职业教育 / 2.学生观",
        "测试考纲 / （二）没有子标题的模块",
    ]
    # 正文以考点名开头（与法条片以「第七条」开头同理）：引用引考点名或引某条要求都得能定位
    assert plans[1].content.startswith("1.教育观")
    assert "要求甲。" in plans[1].content and "要求乙。" in plans[1].content
    # `#` 是 Markdown 记号、不属于原文，必须剥掉，否则引用含标题时会比对不上
    assert "#" not in plans[0].content


def test_有子标题的模块不单独成片():
    """`## 二、内容模块` / `### （一）职业教育` 都是容器，只作为 heading_path 的一环。"""
    paths = [p.heading_path for p in split_syllabus("测试考纲", _BODY)]
    assert not any(p.endswith("二、内容模块") for p in paths)
    assert not any(p.endswith("（一）职业教育") for p in paths)


def test_文档标题不单独成片():
    assert not any(p.heading_path.endswith("文档标题") for p in split_syllabus("测试考纲", _BODY))


def test_没有子标题的模块自成一片():
    """官方文档结构并不统一（如「（四）文化素养」下面没有子标题）——
    规则必须自己认出来，而不是对每个文档手写层级假设。"""
    plans = split_syllabus("测试考纲", _BODY)
    last = plans[-1]
    assert last.heading_path.endswith("（二）没有子标题的模块")
    assert "要求丁。" in last.content


def test_无标题的文档切不出片():
    """切不出片要走 skipped_empty 而不是产出一个空文档。"""
    assert split_syllabus("空文档", "一段没有任何标题的正文。") == []


def test_按目录分发到考纲规则():
    raw = "---\nshort: 某考纲\n---\n\n## 一、目标\n\n内容甲。\n"
    plans = plan_file("syllabus/某考纲.md", raw)
    assert [p.heading_path for p in plans] == ["某考纲 / 一、目标"]
    # 同一个文件放在别的目录里应走段落切分（不是考纲规则）：段落切分把 `## 一、目标`
    # 当成后续段落的 heading，于是首段内容与考纲规则下的 heading_path 完全不同。
    other = plan_file("rubrics/某考纲.md", raw)
    assert [p.heading_path for p in other] == ["一、目标"]


# ---------------- 真实语料 ----------------

#: 科目一全学段三卷考纲（101/201/301）。
#: ⚠️ 与 `考试标准（试行）` **不是同一份文件**：三卷考纲按学段分卷（每卷一份），
#: 考试标准一份覆盖四个学段（幼/小/初中/高中）。两者都放 `syllabus/`（同走考点切片），
#: 所以这里改成**按名列出**而不是数个数 —— 数个数会在新增任何同类语料时误红，
#: 而"多一份 / 少一份"这件事本身仍然要被断言守住（见下一条）。
_EXAM_SYLLABI = ("综合素质-101-幼儿园.md", "综合素质-201-小学.md", "综合素质-301-中学.md")


def test_三卷考纲都在且能切出片():
    for name in _EXAM_SYLLABI:
        f = SYLLABUS_DIR / name
        assert f.exists(), f"科目一全学段三卷考纲缺一不可：{name}"
        plans = plan_file(f"syllabus/{name}", f.read_text(encoding="utf-8"))
        # 18 = 考试目标(1) + 五大模块下的考点(14) + 试卷结构(1) + 题型示例(3) - 1 处容器
        assert len(plans) == 18, f"{name} 片数变化：{len(plans)}"


def test_考纲目录的内容符合预期():
    """目录里多一个文件就多一份检索语料，少一个就是语料缺失 —— **两件都要拦住**，
    所以断言精确集合（新增同类语料时必须**有意识**地改这里，而不是让它悄悄通过）。"""
    expected = set(_EXAM_SYLLABI) | {"考试标准-试行.md"}
    assert {f.name for f in SYLLABUS_DIR.glob("*.md")} == expected


def test_考试标准的一级指标被还原成词():
    """`考试标准（试行）` 的一级指标在官方原文里是**竖排单字**（`职`/`业`/`道`/`德`/`与`…各占一行），
    且与「换行续写的正文」形态完全相同。这条断言守住 `scripts/convert_exam_standard.py`
    的还原结果：拼错或漏字都会在这里红 —— 而这是官方国家标准，丢一个字等于改了标准。
    """
    f = SYLLABUS_DIR / "考试标准-试行.md"
    plans = plan_file(f"syllabus/{f.name}", f.read_text(encoding="utf-8"))
    joined = "\n".join(p.heading_path for p in plans)
    for expect in (
        "幼儿园教师 · 1. 职业道德与基本素养",
        "幼儿园教师 · 3. 保教知识与能力",
        "小学教师 · 1. 职业道德与素养",
        "小学教师 · 3. 教学知识与能力",
        "初中教师 · 2. 教育知识与应用",
        "高中教师 · 3. 教学知识与能力",
    ):
        assert expect in joined, f"一级指标名没还原对：{expect}"
    # 一级名称只剩单字（还原失败）会留下这种痕迹，顺手把它也钉住
    assert " · 养" not in joined and " · 业" not in joined
    # **学段必须在路径里**：否则幼儿园与小学的同名考点（都是 `1.1 职业理念`）路径完全相同，
    # 检索时不可区分 —— 而两份的内容并不一样。这条是踩出来的（第一版把学段放进了被丢掉的容器层）。
    assert "幼儿园教师 · 1. 职业道德与基本素养 / 1.1 职业理念" in joined


def test_考纲片含官方试卷结构比例():
    """试卷结构里官方写明「非选择题约61%」—— 这是计划书里「主观题覆盖 61% 卷面」的**官方依据**，
    不是我们推出来的。它必须真的进了语料，否则那个数字在产品里无法引用。"""
    f = SYLLABUS_DIR / "综合素质-301-中学.md"
    plans = plan_file(f"syllabus/{f.name}", f.read_text(encoding="utf-8"))
    struct = next(p for p in plans if "试卷结构" in p.heading_path)
    flat = re.sub(r"\s+", "", struct.content)
    assert "非选择题：约61%" in flat
    assert "单项选择题：约39%" in flat


def test_官方原文的序号写法被原样保留():
    """官方 .doc 里 `⒈教育观` 与 `2.学生观` 并存 —— 我们不改写成统一格式。
    "逐字"包含序号字符本身：引用引的是官方原文，不是我们整理过的版本。"""
    f = SYLLABUS_DIR / "综合素质-301-中学.md"
    plans = plan_file(f"syllabus/{f.name}", f.read_text(encoding="utf-8"))
    paths = [p.heading_path for p in plans]
    assert any(p.endswith("⒈教育观") for p in paths), "CJK 序号字符被改写了"
    assert any(p.endswith("2.学生观") for p in paths)


def test_逐字保真_源文本的每一行都在语料里():
    """单调扫描：官方原文每一非空行必须**按顺序**出现在语料里。

    只要求"按顺序出现"，所以补标题层级这类插入不算改动；
    但丢字、改字、改不可见空白（全角空格 U+3000 ↔ 普通空格）都会被抓出来 ——
    后者是本轮真实踩过的坑：**76 行只差看不见的空白**，肉眼完全分辨不出。
    """
    import pytest

    if not RAW_DIR.exists():
        pytest.skip("官方原始文本未保留（.syllabus_tmp/ 不随仓库走），跳过保真断言")

    for md_name, txt_name in _REAL_FILES:
        src = RAW_DIR / txt_name
        dst = SYLLABUS_DIR / md_name
        if not src.exists() or not dst.exists():
            pytest.skip(f"缺少 {txt_name}")
        md = dst.read_text(encoding="utf-8")
        pos = 0
        for line in (ln.strip() for ln in src.read_text(encoding="utf-8").splitlines()):
            if not line:
                continue
            found = md.find(line, pos)
            assert found >= 0, f"{md_name} 中未按顺序找到：{line[:60]!r}"
            pos = found + len(line)
