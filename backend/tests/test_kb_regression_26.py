"""#26 回归测试：把本轮修复的**结构性行为**固定下来，防止后续改动悄悄退回旧语义。

说明：这些用例用可控 stub 驱动，不依赖真实 LLM 的输出质量，可离线快速运行——
它们锁的是「分支与降级语义」（自检全否怎么办、抽检调几次、层级怎么分、两个池是否分开），
而**生成质量与耗时**由真实环境验证负责（见项目冒烟脚本与真实出题记录）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services.kb_generate import (
    _ctx_for_question,
    _generate_batch_with_fallback,
    _selfcheck_batch,
)
from app.services.prompts_kb import bloom_distribution
from app.services.task_pool import EMBED, GEN, get_pool
from app.utils import local_day, near_duplicate


class _StubClient:
    """可控 stub：自检通过与否、生成的 payload 均可指定，并记录 ask 调用次数。"""

    def __init__(self, selfcheck_passed: bool = True, payloads: list[dict] | None = None):
        self.selfcheck_passed = selfcheck_passed
        self.payloads = payloads or []
        self.ask_calls: list[str] = []

    def ask(self, prompt, timeout=30):
        self.ask_calls.append(prompt)
        if "【生成自检】" in prompt:
            return json.dumps(
                {
                    "passed": self.selfcheck_passed,
                    "score": 1.0 if self.selfcheck_passed else 0.0,
                    "issues": [],
                }
            )
        return json.dumps({"questions": self.payloads})


def _payload(stem: str, source_id: int | None = None) -> dict:
    """一条能通过 validate_question_payload 的最小题目。"""
    return {
        "module": "个人资料",
        "knowledge_point": "测试考点",
        "stem": stem,
        "options": [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
        "answer": ["A"],
        "explanation": "依据资料",
        "type": "single",
        "source_id": source_id,
    }


# ---------------- 本地日统计（#26 遗留 1：时区） ----------------

def test_local_day_none_returns_empty():
    assert local_day(None) == ""


def test_local_day_naive_is_treated_as_utc():
    dt = datetime(2026, 9, 11, 20, 0)  # naive
    assert local_day(dt) == dt.replace(tzinfo=timezone.utc).astimezone().date().isoformat()


def test_local_day_aware_converted_to_local_date():
    dt = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
    assert local_day(dt) == dt.astimezone().date().isoformat()


# ---------------- 认知层级分布（#26 A） ----------------

def test_bloom_distribution_even_split():
    assert bloom_distribution(6, ["understand", "apply", "analyze"]) == {
        "understand": 2,
        "apply": 2,
        "analyze": 2,
    }


def test_bloom_distribution_remainder_goes_to_earlier_levels():
    mix = bloom_distribution(5, ["understand", "apply", "analyze"])
    assert sum(mix.values()) == 5
    assert mix["understand"] == 2  # 余数优先给靠前层级


def test_bloom_distribution_edge_cases():
    assert bloom_distribution(0, ["understand"]) == {}
    assert bloom_distribution(3, []) == {}


# ---------------- 自检依据按题溯源（#26 A-1） ----------------

def test_ctx_for_question_prefers_own_source_slice():
    chunks = [{"id": 1, "content": "第一片内容"}, {"id": 2, "content": "第二片内容"}]
    assert _ctx_for_question({"source_id": 2}, chunks, 8000) == "第二片内容"


def test_ctx_for_question_falls_back_to_concat():
    chunks = [{"id": 1, "content": "甲"}, {"id": 2, "content": "乙"}]
    assert _ctx_for_question({}, chunks, 8000) == "甲\n乙"


# ---------------- 自检抽检（#26 P1） ----------------

def test_selfcheck_sample_pass_returns_all_with_fewer_calls():
    payloads = [_payload("题1"), _payload("题2"), _payload("题3")]
    client = _StubClient(selfcheck_passed=True)
    passed, all_ok = _selfcheck_batch(client, payloads, [], sample=2)
    assert all_ok is True
    assert len(passed) == 3           # 抽检通过 → 整批放行
    assert len(client.ask_calls) == 2  # 只检 2 题，不是 3 题


def test_selfcheck_sample_reject_upgrades_to_full_check():
    payloads = [_payload("题1"), _payload("题2"), _payload("题3")]
    client = _StubClient(selfcheck_passed=False)
    passed, all_ok = _selfcheck_batch(client, payloads, [], sample=2)
    assert all_ok is False
    assert passed == []  # 全部不通过


# ---------------- 核心：自检全否不欠产（#26 P0 语义） ----------------

def test_fallback_keeps_rule_passed_items_when_selfcheck_rejects_all():
    """自检的意义是标记风险，不应成为欠产的原因。

    旧语义 `payloads = regen or payloads` 在自检全否时会返回空 → 大卷 0 产出。
    """
    client = _StubClient(selfcheck_passed=False, payloads=[_payload("唯一题目")])
    got = _generate_batch_with_fallback(
        client, "scope", "single", 1, "medium", None, [], set(), [], emit=None,
    )
    assert len(got) == 1
    assert got[0]["stem"] == "唯一题目"


def test_fallback_respects_count_cap():
    client = _StubClient(selfcheck_passed=True, payloads=[_payload("甲题"), _payload("乙题"), _payload("丙题")])
    got = _generate_batch_with_fallback(
        client, "scope", "single", 2, "medium", None, [], set(), [], emit=None,
    )
    assert len(got) == 2


# ---------------- 题干近似判重（#26 遗留 3） ----------------

def test_near_duplicate_catches_space_only_difference():
    """精确去重拦不住的「仅差空格」重复。"""
    a = "根据资料切片 #169，教育区别于其他社会活动的根本特征是什么？"
    b = "根据资料切片#169，教育区别于其他社会活动的根本特征是什么？"
    assert near_duplicate(a, b, 0.6) is True


def test_near_duplicate_catches_reworded_duplicate():
    """真实场景出现过的措辞不同但语义相同的重复（实测相似度约 0.68）。"""
    a = "根据资料切片 #169，教育区别于其他社会活动的根本特征是什么？"
    b = "根据资料切片#169，教育区别于其他社会活动的根本特征，可归纳为以下哪一项？"
    assert near_duplicate(a, b, 0.6) is True


def test_near_duplicate_rejects_unrelated_questions():
    assert near_duplicate("教育的本质是什么？", "教学原则包括哪些内容？", 0.6) is False


def test_near_duplicate_handles_empty():
    assert near_duplicate("", "", 0.6) is False


# ---------------- 任务池分池（#26 P2） ----------------

def test_gen_and_embed_pools_are_separate():
    """分池是防死锁的关键：出题任务会等向量化，共用池会互抢 worker。"""
    assert get_pool(GEN) is not get_pool(EMBED)
