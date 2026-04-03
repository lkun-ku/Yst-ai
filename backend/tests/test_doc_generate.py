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
    # #26：分配策略由「按段长比例」改为「按章节均匀分配**（避免长章节垄断题量）。
    # 下面按新语义断言。

    def test_均匀分配_不被长章节垄断(self):
        # 长度差 3 倍，均匀分配下差距最多 1 题（比例分配会得到 [1, 3]。
        segs = [{"char_count": 100}, {"char_count": 300}]
        out = allocate_quota(segs, 4)
        assert out == [2, 2]
        assert sum(out) == 4

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

    def test_余数给内容最多的段(self):
        # base=1，余 1 题应给最长的段（体现「长章节略多」）
        segs = [{"char_count": 50}, {"char_count": 500}, {"char_count": 100}]
        out = allocate_quota(segs, 4)
        assert sum(out) == 4
        assert out == [1, 2, 1]

    def test_单段全量(self):
        assert allocate_quota([{"char_count": 100}], 5) == [5]

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
