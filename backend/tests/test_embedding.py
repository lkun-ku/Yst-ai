"""Embedding 与文档级语义检索的测试（TDD 红→绿）。

重点验证「三级降级」：向量 → 关键词 → 均匀采样，任一环节失效都不得阻塞出题。
"""
import pytest

from app.services.embedding import (
    cosine_similarity,
    decode_vector,
    embed_one,
    encode_vector,
    keyword_score,
    retrieve,
    uniform_sample,
)


def _chunk(seq, content, heading=None, vec=None, status="pending"):
    c = {
        "seq": seq,
        "content": content,
        "heading_path": heading,
        "embedding": encode_vector(vec) if vec else None,
        "embed_status": status if vec else "failed",
    }
    return c


class TestVectorCodec:
    def test_编解码往返(self):
        v = [0.1, 0.2, -0.3, 0.4]
        out = decode_vector(encode_vector(v))
        assert len(out) == len(v)
        for a, b in zip(v, out):
            assert a == pytest.approx(b, abs=1e-6)

    def test_cosine_自身为1(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_cosine_正交为0(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-6)

    def test_cosine_零向量不崩(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestFakeEmbedding:
    def test_确定性_同文本同向量(self):
        assert embed_one("教育的本质") == embed_one("教育的本质")

    def test_不同文本向量不同(self):
        assert embed_one("教育的本质") != embed_one("汽车发动机原理")


class TestKeywordScore:
    def test_命中得分大于零(self):
        assert keyword_score("教育的本质", "教育的本质是培养人的社会活动") > 0

    def test_不命中为零(self):
        assert keyword_score("量子物理", "教育的本质是培养人的社会活动") == 0


class TestRetrieve:
    def test_通道1_向量命中相关片段(self):
        a = _chunk(0, "教育的本质是培养人的社会活动", vec=embed_one("教育的本质是培养人的社会活动"))
        b = _chunk(1, "汽车发动机的原理与维修保养", vec=embed_one("汽车发动机的原理与维修保养"))
        got = retrieve("教育的本质", [a, b], k=1)
        assert got[0]["seq"] == 0

    def test_通道2_无向量时降级关键词(self):
        a = _chunk(0, "教育的本质是培养人的社会活动")  # 无 embedding
        b = _chunk(1, "汽车发动机的原理与维修保养")
        got = retrieve("教育的本质", [a, b], k=1)
        assert got[0]["seq"] == 0
        assert got[0]["embed_status"] == "failed"  # 确认走的是降级路径

    def test_通道3_关键词也不命中时均匀采样(self):
        pool = [_chunk(i, f"完全无关的内容{i}", vec=None) for i in range(10)]
        got = retrieve("量子物理前沿", pool, k=3)
        assert len(got) == 3
        # 均匀采样应覆盖首尾，而非只取前三
        assert got[0]["seq"] == 0
        assert got[-1]["seq"] > 3

    def test_scope_过滤生效(self):
        a = _chunk(0, "第一章的内容", heading="第一章 教育基础")
        b = _chunk(1, "第二章的内容", heading="第二章 教学原理")
        got = retrieve("内容", [a, b], k=5, scope=["第二章 教学原理"])
        assert [c["seq"] for c in got] == [1]

    def test_scope_无匹配时回退全量(self):
        a = _chunk(0, "第一章的内容", heading="第一章 教育基础")
        got = retrieve("内容", [a], k=5, scope=["不存在的章节"])
        assert len(got) == 1

    def test_空片段池不崩(self):
        assert retrieve("任意", [], k=3) == []


class TestUniformSample:
    def test_数量足够时覆盖首尾(self):
        pool = list(range(10))
        out = uniform_sample(pool, 3)
        assert out == [0, 3, 6]

    def test_k_大于池长度时返回全部(self):
        assert uniform_sample([1, 2], 5) == [1, 2]
