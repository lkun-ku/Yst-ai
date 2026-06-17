"""S1 连胜与补签卡的纯逻辑测试（TDD 红→绿）。"""
import pytest

from app.services.streak import (
    MAX_MAKEUP_CARDS,
    advance,
    cards_for_milestone,
    empty_state,
    grant_cards,
    makeup,
    next_day,
    prev_day,
)


class TestAdvance:
    def test_首次完成_连胜为1(self):
        s = advance(empty_state(), "2026-03-05")
        assert s["current"] == 1
        assert s["last_date"] == "2026-03-05"
        assert s["max"] == 1

    def test_连续两天_连胜累加(self):
        s = advance(empty_state(), "2026-03-05")
        s = advance(s, "2026-03-06")
        assert s["current"] == 2

    def test_同一天重复完成_幂等(self):
        s = advance(empty_state(), "2026-03-05")
        s2 = advance(s, "2026-03-05")
        assert s2["current"] == 1  # 不重复 +1
        assert s2 == s

    def test_断连后重新从1开始(self):
        s = advance(empty_state(), "2026-03-05")
        s = advance(s, "2026-03-06")  # 2
        s = advance(s, "2026-03-09")  # 隔了 3 天 → 断连
        assert s["current"] == 1

    def test_max_记录历史最高(self):
        s = advance(empty_state(), "2026-03-05")
        s = advance(s, "2026-03-06")
        s = advance(s, "2026-03-07")
        assert s["max"] == 3
        s = advance(s, "2026-03-14")  # 断连
        assert s["current"] == 1
        assert s["max"] == 3  # 历史最高保留

    def test_total_days_累计达标天数(self):
        s = advance(empty_state(), "2026-03-05")
        s = advance(s, "2026-03-06")
        s = advance(s, "2026-03-13")
        assert s["total_days"] == 3


class TestMakeup:
    def _state(self, cards=1):
        s = advance(empty_state(), "2026-03-05")  # current=1, last=09-01
        s = advance(s, "2026-03-07")  # 断连 → current=1, last=09-03
        s["cards"] = cards
        return s

    def test_补签成功_连胜加一且扣卡(self):
        s = self._state(cards=2)
        ok, ns, why = makeup(s, "2026-03-06", used=set())
        assert ok is True
        assert ns["current"] == 2
        assert ns["cards"] == 1

    def test_无卡不可补签(self):
        s = self._state(cards=0)
        ok, ns, why = makeup(s, "2026-03-06", used=set())
        assert ok is False
        assert "补签卡" in why
        assert ns["cards"] == 0

    def test_同一天不可重复补签(self):
        s = self._state(cards=3)
        ok1, s1, _ = makeup(s, "2026-03-06", used=set())
        assert ok1 is True
        ok2, s2, why = makeup(s1, "2026-03-06", used={"2026-03-06"})
        assert ok2 is False
        assert "已补签" in why
        assert s2["current"] == s1["current"]  # 未再增加

    def test_不可补今天或未来(self):
        s = self._state(cards=3)
        ok, _, why = makeup(s, "2026-03-07", used=set())  # == last_date
        assert ok is False
        ok, _, why = makeup(s, "2026-03-14", used=set())  # 未来
        assert ok is False
        assert "已过去" in why

    def test_无连胜记录不可补签(self):
        ok, _, why = makeup(empty_state(), "2026-03-05", used=set())
        assert ok is False
        assert "还没有连胜" in why


class TestGrantCards:
    def test_发卡受上限约束(self):
        s = empty_state()
        for _ in range(10):
            s = grant_cards(s, 1)
        assert s["cards"] == MAX_MAKEUP_CARDS

    def test_发卡累加(self):
        s = grant_cards(empty_state(), 2)
        assert s["cards"] == 2

    def test_里程碑只发一次(self):
        assert cards_for_milestone(3, set()) == 1
        assert cards_for_milestone(3, {3}) == 0
        assert cards_for_milestone(7, {3}) == 1
        assert cards_for_milestone(14, {3, 7}) == 1
        assert cards_for_milestone(14, {3, 7, 14}) == 0


class TestDateHelpers:
    def test_next_day_跨月(self):
        assert next_day("2026-03-31") == "2026-04-01"
        assert next_day("2026-05-31") == "2026-06-01"

    def test_prev_day_跨年(self):
        assert prev_day("2026-06-01") == "2026-05-31"


class TestNoShareUpsell:
    """红线：补签卡不得来自分享/邀请/广告。本模块不提供任何此类入口。"""

    def test_模块不导出分享相关接口(self):
        import app.services.streak as m
        exported = {n for n in dir(m) if not n.startswith("_")}
        assert not any(k in exported for k in ("share", "invite", "ad", "advert"))

    def test_仅_grant_cards_能增加卡(self):
        s = empty_state()
        s2 = dict(s)
        # 除 grant_cards 外，advance / makeup 都不应增加卡
        s3 = advance(s2, "2026-03-05")
        assert s3["cards"] == s["cards"]
        ok, s4, _ = makeup({**s3, "cards": 1}, "2026-03-04", used=set())
        assert s4["cards"] <= 1


def test_idempotent_full_flow():
    """完整流程：连胜 → 断连 → 补签 → 继续连胜。"""
    s = empty_state()
    for d in ("2026-03-05", "2026-03-06", "2026-03-07"):
        s = advance(s, d)
    assert s["current"] == 3
    s = grant_cards(s, 1)
    s = advance(s, "2026-03-10")  # 断连 → 1
    assert s["current"] == 1
    ok, s, _ = makeup(s, "2026-03-09", used=set())
    assert ok and s["current"] == 2
    s = advance(s, "2026-03-11")  # 06 之后连续 → 3
    assert s["current"] == 3
