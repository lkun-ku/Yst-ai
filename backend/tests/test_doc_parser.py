"""文档解析与切分的纯逻辑测试（TDD 红→绿）。

只依赖标准库，不需要真实 PDF/DOCX 文件，保证快且稳。
"""
import pytest

from app.services.doc_parser import (
    clean_text,
    detect_no_text_layer,
    join_chunks,
    split_chunks,
    normalize_stem,
)


class TestJoinChunks:
    """`join_chunks`：把切片拼回连续全文。

    它守的是「资料详情页看不到原文」这条实测反馈的修法。**滑窗切分带 overlap**，
    直接首尾相接会把每个边界重复一遍（读起来像卡带），所以这里钉三件事：
    拼回去要**等于原文**、重叠**恰好**被消掉、以及"不一致时不乱裁"。
    """

    def test_拼回等于原文(self):
        """**端到端性质**：切分 → 拼接，必须还原原文（这是这段逻辑存在的全部意义）。"""
        text = "".join(f"第{i}句：教育是有目的地培养人的社会活动。" for i in range(400))
        chunks = [c["content"] for c in split_chunks(text, chunk_size=180, overlap=40)]
        assert len(chunks) > 3, "样本要真的切出多片，否则这条测试是空的"
        assert join_chunks(chunks) == clean_text(text)

    def test_重叠段恰好被消掉(self):
        # 上一片结尾与下一片开头重复 6 字
        assert join_chunks(["甲乙丙丁戊己庚辛", "戊己庚辛壬癸"]) == "甲乙丙丁戊己庚辛壬癸"

    def test_没有重叠时原样拼接(self):
        assert join_chunks(["第一段内容。", "第二段内容。"]) == "第一段内容。第二段内容。"

    def test_裁切量不超过上限_宁可留重复不可丢内容(self):
        """上限 `max_overlap` 是**有意的**：两片若真的碰巧首尾相同很长，无上限地裁就会
        切进真实内容（**丢内容比留一段重复更难被发现**）。

        这里钉的是那个取舍的确切形态：只裁到上限，剩下的重复**留着**，真实内容一字不丢。
        """
        prev = "甲" * 30
        cur = "甲" * 30 + "新的内容"

        out = join_chunks([prev, cur], max_overlap=10)

        assert out.endswith("新的内容"), "真实内容不能丢"
        assert len(out) == 30 + 30 + 4 - 10, "只应裁掉上限内的 10 字"

    def test_空切片不影响拼接(self):
        assert join_chunks(["第一段。", "", "第二段。"]) == "第一段。第二段。"
        assert join_chunks([]) == ""


class TestCleanText:
    def test_合并中文断行(self):
        # pypdf 常见：一个词被拆到两行
        raw = "教育的本质属性是\n培养人的社会活动。"
        out = clean_text(raw)
        assert "教育的本质属性是培养人的社会活动。" in out

    def test_句末标点不合并(self):
        raw = "这是第一句。\n这是第二句。"
        out = clean_text(raw)
        assert out.count("\n") == 1  # 保持两行

    def test_剔除重复页眉页脚(self):
        header = "教师资格证考试专用"
        lines = []
        for i in range(15):
            lines.append(f"正文第{i}行的具体内容，用于避免被判为重复行。")
            lines.append(header)  # 每页都出现的页眉
        out = clean_text("\n".join(lines))
        assert header not in out
        assert "正文第0行的具体内容，用于避免被判为重复行。" in out

    def test_剔除纯页码行(self):
        raw = "正文内容足够长的一行文字。\n42\n继续下一页的正文内容也足够长。"
        out = clean_text(raw)
        assert "\n42\n" not in out

    def test_空输入不崩(self):
        assert clean_text("") == ""
        assert clean_text(None) == ""


class TestDetectNoTextLayer:
    def test_有文字层(self):
        assert detect_no_text_layer("这是一页有足够文字的内容。" * 20, 1) is False

    def test_扫描件无文字层(self):
        assert detect_no_text_layer("", 10) is True
        assert detect_no_text_layer("  ", 10) is True

    def test_每页平均字符过低(self):
        assert detect_no_text_layer("短", 50) is True


class TestSplitChunks:
    def test_短文本单切片(self):
        chunks = split_chunks("这是一段短文本。", chunk_size=100, overlap=20)
        assert len(chunks) == 1
        assert chunks[0]["content"] == "这是一段短文本。"

    def test_长文本滑窗且有重叠(self):
        text = "甲" * 1000
        chunks = split_chunks(text, chunk_size=300, overlap=50)
        assert len(chunks) > 1
        # 相邻切片应有重叠：第 n+1 片开头 == 第 n 片结尾往前 overlap 处
        assert chunks[0]["content"][-50:] in chunks[1]["content"][:60]

    def test_章节标题生成_heading_path(self):
        text = "第一章 教育基础\n" + "内容甲" * 200 + "\n第二章 教学原理\n" + "内容乙" * 200
        chunks = split_chunks(text, chunk_size=300, overlap=50)
        paths = {c["heading_path"] for c in chunks}
        assert "第一章 教育基础" in paths
        assert "第二章 教学原理" in paths

    def test_无标题时_heading_path_为空(self):
        chunks = split_chunks("没有标题的一段内容。" * 50, chunk_size=200, overlap=40)
        assert all(c["heading_path"] is None for c in chunks)

    def test_seq_连续且_char_count_正确(self):
        chunks = split_chunks("甲乙丙" * 500, chunk_size=200, overlap=40)
        assert [c["seq"] for c in chunks] == list(range(len(chunks)))
        for c in chunks:
            assert c["char_count"] == len(c["content"])

    def test_空文本不产生切片(self):
        assert split_chunks("", chunk_size=100) == []
        assert split_chunks("   ", chunk_size=100) == []


class TestNormalizeStem:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("教育的本质是什么？", "教育的本质是什么?"),  # 全半角
            ("教育的本质", "  教育的本质  "),  # 空白
            ("Teacher", "teacher"),  # 大小写
        ],
    )
    def test_等价题干归一化后相同(self, a, b):
        assert normalize_stem(a) == normalize_stem(b)

    def test_不同题干归一化后不同(self):
        assert normalize_stem("教育的本质") != normalize_stem("教学的原则")
