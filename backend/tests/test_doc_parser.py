"""文档解析与切分的纯逻辑测试（TDD 红→绿）。

只依赖标准库，不需要真实 PDF/DOCX 文件，保证快且稳。
"""
import pytest

from app.services.doc_parser import (
    clean_text,
    detect_no_text_layer,
    split_chunks,
    normalize_stem,
)


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
