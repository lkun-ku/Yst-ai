"""出题管线的纯逻辑测试（TDD）：配比归一化、分批、章节配额分配。

配额分配是「整卷模式」的核心——保证题目覆盖全文档，不扎堆在语义密度最高的章节。
"""
import pytest

from app.services.doc_generate import (
    allocate_quota,
    build_batches,
    normalize_spec,
)


class TestNormalizeSpec:
    def test_过滤非法题型与非正数(self):
        spec = [
            {"type": "single", "count": 5},
            {"type": "不存在的题型", "count": 5},
            {"type": "blank", "count": 0},
            {"type": "judge", "count": -1},
        ]
        assert normalize_spec(spec) == [{"type": "single", "count": 5}]

    def test_总题量超限被截断(self):
        spec = [{"type": "single", "count": 20}, {"type": "blank", "count": 20}]
        out = normalize_spec(spec, max_total=30)
        assert sum(i["count"] for i in out) == 30

    def test_未超限保持不变(self):
        spec = [{"type": "single", "count": 3}]
        assert normalize_spec(spec, max_total=30) == [{"type": "single", "count": 3}]

    def test_空输入(self):
        assert normalize_spec(None) == []
        assert normalize_spec([]) == []


class TestBuildBatches:
    def test_按批大小切分(self):
        spec = [{"type": "single", "count": 10}]
        assert build_batches(spec, batch_size=6) == [("single", 6), ("single", 4)]

    def test_多题型分别成批(self):
        spec = [{"type": "single", "count": 3}, {"type": "blank", "count": 2}]
        assert build_batches(spec, batch_size=6) == [("single", 3), ("blank", 2)]

    def test_不足一批(self):
        assert build_batches([{"type": "judge", "count": 2}], batch_size=6) == [("judge", 2)]

    def test_零数量不成批(self):
        assert build_batches([{"type": "judge", "count": 0}], batch_size=6) == []


class TestAllocateQuota:
    def test_按长度比例分配(self):
        segs = [{"char_count": 100}, {"char_count": 300}]
        assert allocate_quota(segs, 4) == [1, 3]

    def test_总题量守恒(self):
        segs = [{"char_count": 120}, {"char_count": 80}, {"char_count": 200}]
        out = allocate_quota(segs, 17)
        assert sum(out) == 17

    def test_均分场景(self):
        segs = [{"char_count": 100}, {"char_count": 100}, {"char_count": 100}]
        out = allocate_quota(segs, 9)
        assert out == [3, 3, 3]

    def test_题量少于段数时部分为0且守恒(self):
        segs = [{"char_count": 10}, {"char_count": 10}, {"char_count": 10}]
        out = allocate_quota(segs, 2)
        assert sum(out) == 2
        assert sorted(out, reverse=True) == [1, 1, 0]

    def test_零长段落均分(self):
        segs = [{"char_count": 0}, {"char_count": 0}]
        out = allocate_quota(segs, 3)
        assert sum(out) == 3

    def test_空段落(self):
        assert allocate_quota([], 5) == []

    def test_零题量(self):
        assert allocate_quota([{"char_count": 100}], 0) == [0]

    def test_单段独占(self):
        assert allocate_quota([{"char_count": 999}], 5) == [5]
