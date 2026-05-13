"""《中小学和幼儿园教师资格考试标准（试行）》→ 结构化 Markdown（**机械转换，不手抄**）。

## 为什么不手抄

原文（`data/official/_source/考试标准_试行.txt`）是 Word 导出的**合并单元格表格**，
且一级指标名被导成了**竖排单字**：

    　　1
    　　职
    　　业
    　　道
    　　德
    　　与
    基本
    　　素
    　　养	1.1职业理念	1.1.1关爱幼儿……

人眼能拼出「职业道德与基本素养」，但**手工转写一定会悄悄丢字或串行** ——
而这是官方国家标准，丢一个字就等于改了标准。所以必须机械转换 + 逐条计数核对。

## 解析要点：怎么把「竖排单字」和「换行续写的正文」分开

两者形态**相同**（都是短行、都不含制表符），只看长度分不开。可用的确定性信号是**位置**：

| 信号 | 含义 |
| --- | --- |
| 单独一行只有数字（`1` / `2` / `3`） | **一级指标块开始**（竖排从这里起） |
| 某单元格匹配 `N.N`（如 `1.1职业理念`） | 一级块**结束**，同时开启一个二级指标 |
| 某单元格匹配 `N.N.N`（如 `1.1.1…`） | 一条三级指标 |
| 其余短行且不在竖排块内 | 上一条三级指标的**换行续写**（如 `　意识。`） |

于是用一个小状态机即可，判定全部来自结构、不靠长度猜测。

## 输出结构

`## 一级指标` → `### N.N 二级指标`（**叶子标题**，正文是各条三级指标）。
这与 `kb_corpus.split_syllabus` 的口径一致：一个考点一片，`heading_path`
给出「文档 / 一级指标 / 二级指标」—— 考纲的语义恰恰在归属上
（「面向全体学生」是**素质教育观**的要求，不是教师观的）。

跑法：`python scripts/convert_exam_standard.py`
"""

from __future__ import annotations

import pathlib
import re
import sys

_SRC = pathlib.Path(__file__).resolve().parents[1] / "data" / "official" / "_source" / "考试标准_试行.txt"
_OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "official" / "syllabus" / "考试标准-试行.md"

#: 学段分段：`（一）幼儿园教师` / `（二）小学教师` / `（三）中学教师`
_STAGE_RE = re.compile(r"^\s*（[一二三四五六七八九十]）\s*(.+?)\s*$")
#: 二级指标：`1.1职业理念`（**注意**：先按三级判，否则 `1.1.1…` 会被二级抢走）
_L2_RE = re.compile(r"^(\d+\.\d+)\s*(.*)$")
_L3_RE = re.compile(r"^(\d+\.\d+\.\d+)\s*(.*)$")
#: 一级指标块的起始：单独一行的数字
_L1_NUM_RE = re.compile(r"^\s*(\d+)\s*$")


class Level2:
    def __init__(self, num: str, name: str) -> None:
        self.num = num
        self.name = name
        self.reqs: list[list[str]] = []  # [条号, 内容]，内容可能由多行拼成

    def absorb(self, text: str) -> None:
        """一条三级指标（`N.N.N 内容`）或它的换行续写。"""
        m = _L3_RE.match(text)
        if m:
            self.reqs.append([m.group(1), m.group(2).strip()])
        elif self.reqs:
            # 续写：直接接上，不加空格 —— 原文是被 Word 折断的同一句话
            self.reqs[-1][1] += text.strip()


class Level1:
    def __init__(self, num: str) -> None:
        self.num = num
        self.name_parts: list[str] = []
        self.items: list[Level2] = []

    @property
    def name(self) -> str:
        return "".join(self.name_parts)


def parse(text: str) -> tuple[list[str], dict[str, list[Level1]]]:
    """→ (文档前言, {学段: [一级指标]})。

    `前言` 是第一个学段小标题之前的内容（制定依据 + 考试目标），
    它不属于任何学段，但**是标准正文的一部分**，不能丢。
    """
    preamble: list[str] = []
    stages: dict[str, list[Level1]] = {}
    stage: str | None = None
    in_vertical = False
    cur1: Level1 | None = None
    cur2: Level2 | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        # 表格表头行（`一级指标	二级指标	三级指标`）——结构噪声，丢了它输出才干净
        if line.startswith("一级") or "二级指标" in line:
            continue

        m_stage = _STAGE_RE.match(line)
        if m_stage and line.strip().startswith("（"):
            stage = m_stage.group(1)
            stages.setdefault(stage, [])
            in_vertical = False
            cur1 = cur2 = None
            continue

        if stage is None:
            preamble.append(line.strip())
            continue

        cells = [c.strip() for c in line.split("\t")]

        # 1) 二级指标（一级块在此结束）
        for idx, c in enumerate(cells):
            if _L3_RE.match(c):
                continue
            m2 = _L2_RE.match(c)
            if not m2:
                continue
            in_vertical = False
            # 二级之前的单元格是竖排块的最后几个字（如 `　　养`）
            for pre in cells[:idx]:
                if pre and cur1 is not None:
                    cur1.name_parts.append(pre)
            cur2 = Level2(m2.group(1), m2.group(2).strip())
            cur1.items.append(cur2)
            # 二级之后的单元格可能已经带了一条三级
            for post in cells[idx + 1 :]:
                if post:
                    cur2.absorb(post)
            break
        else:
            # 2) 单独数字 → 一级指标块开始
            if len(cells) == 1 and _L1_NUM_RE.match(cells[0]):
                cur1 = Level1(cells[0])
                stages[stage].append(cur1)
                cur2 = None
                in_vertical = True
                continue
            # 3) 竖排块内 → 一级名称的一部分
            #: 一个单元格最多几个字才算竖排的一部分。竖排是"一个字一格"，
            #: 最长的格是 `知识与应用`（5 字）；而正文续写一般更长。
            #: 但**判别主要靠 in_vertical**，这个上限只是兜底。
            if in_vertical and all(len(c) <= 6 for c in cells):
                if cur1 is not None:
                    for c in cells:
                        if c:
                            cur1.name_parts.append(c)
                continue
            # 4) 其余 → 上一条三级指标的续写
            if cur2 is not None:
                for c in cells:
                    if c:
                        cur2.absorb(c)

    return preamble, stages


def render(preamble: list[str], stages: dict[str, list[Level1]]) -> str:
    lines = [
        "---",
        "short: 中小学和幼儿园教师资格考试标准（试行）",
        "doc_form: 合并单元格表格（Word 导出）",
        "publisher: 教育部师范教育司 / 教育部考试中心",
        "issued: 二〇一一年十月",
        "source_file: data/official/_source/考试标准_试行.txt",
        "retrieved: 2026-06-11",
        "converter: scripts/convert_exam_standard.py",
        "note: 正文逐字取自官方原文，由机械转换器生成（一级指标的竖排单字已还原成词）；"
        "仅去掉表格结构所需的制表符与全角缩进、补 Markdown 标题层级，未改动任何文字、标点与数字。",
        "---",
        "",
        "# 中小学和幼儿园教师资格考试标准（试行）",
        "",
    ]
    # 前言里的 `一、考试目标` 等小标题补成 Markdown 二级标题
    for line in preamble:
        if re.match(r"^[一二三四五六七八九十]+、", line):
            lines += [f"## {line}", ""]
        else:
            lines += [line, ""]
    for stage, l1s in stages.items():
        for l1 in l1s:
            # ⚠️ **学段必须写进这一行**，不能另起一个 `## 学段` 容器。
            # `kb_corpus.split_syllabus` 会丢掉容器层（`##`），只把「考点那一层」及其父层
            # 放进 heading_path —— 于是 `幼儿园 / 1. 职业道德与基本素养` 与
            # `小学 / 1. 职业道德与基本素养` 会得到**完全相同的路径**，检索时无法区分
            # （而这两份的考点内容并不相同）。写进标题里，路径才带得住学段。
            lines += [f"### {stage} · {l1.num}. {l1.name}", ""]
            for l2 in l1.items:
                lines += [f"#### {l2.num} {l2.name}", ""]
                for num, text in l2.reqs:
                    lines += [f"（{num}）{text}", ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if not _SRC.exists():
        print(f"源文件不存在：{_SRC}", file=sys.stderr)
        return 1
    preamble, stages = parse(_SRC.read_text(encoding="utf-8"))
    _OUT.write_text(render(preamble, stages), encoding="utf-8")

    # 计数核对：转换是否漏段，靠数字看，不靠通读
    print(f"已写入：{_OUT}")
    print(f"前言 {len(preamble)} 行")
    for stage, l1s in stages.items():
        n2 = sum(len(l1.items) for l1 in l1s)
        n3 = sum(len(l2.reqs) for l1 in l1s for l2 in l1.items)
        unnamed = [l1.num for l1 in l1s if not l1.name]
        print(f"  {stage}: 一级 {len(l1s)} · 二级 {n2} · 三级 {n3}"
              + (f"  ⚠️ 一级名称未还原：{unnamed}" if unnamed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
