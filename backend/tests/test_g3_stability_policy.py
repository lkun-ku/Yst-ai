"""G3'「稳定性该不该单独触发拦截」——判定口径与离线反算脚本的回归测试。

## 背景（2026-06-16）

在 254 道官方好题 + 36 道歧义题上**离线反算**（`eval/g3_stability_ab.py`，不调模型）：

| | 现状（稳定性可单独拦） | 关掉稳定性单独拦 |
| --- | --- | --- |
| 官方好题误杀率 | 4.72%（12/254） | 3.54%（9/254） |
| 歧义题拦截率 | 61.11%（22/36） | 55.56%（20/36） |

结论：**收益确定但很小（3 道），代价不确定且可能更大（2/36）** —— 所以默认维持严格口径，
但把口径做成开关，并且两个口径**都不放松 `matches`**。

## 为什么值得单独写测试

"关掉稳定性"最容易被误读成"放宽标准"。它不是：放行与否仍以"多数表决结果 == 答案键"为准，
开关只决定**要不要额外要求几次判定一致**。这个区别一旦被写错，闸门会静默放行判错的题，
而没有任何报错（这类错误只有断言能抓住）。
"""

from __future__ import annotations

import json
import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import settings  # noqa: E402
from app.services.quality_gates import OptionVerdict  # noqa: E402


def _v(stable: bool, matches: bool, n: int = 2) -> OptionVerdict:
    votes = (("C",), ("C",)) if stable else (("C",), ("B",))
    judged = ("C",) if matches else ("B",)
    return OptionVerdict(votes=votes, judged=judged, stable=stable, matches=matches, n=n)


def test_严格口径下不稳定即拦截(monkeypatch):
    monkeypatch.setattr(settings, "gate_g3_block_on_instability", True)
    assert _v(stable=False, matches=True).passed_under_policy is False
    assert _v(stable=True, matches=True).passed_under_policy is True


def test_关掉后不稳定的题也放行(monkeypatch):
    monkeypatch.setattr(settings, "gate_g3_block_on_instability", False)
    assert _v(stable=False, matches=True).passed_under_policy is True


def test_两个口径都不放松_matches(monkeypatch):
    """**这条守的是最容易误读的一点**：关掉稳定性 ≠ 放宽标准。

    多数表决结果与答案键不符时，两种口径**都必须**拦截 ——
    否则"关掉稳定性"会变成"判错也放行"，闸门形同虚设且无人察觉。
    """
    monkeypatch.setattr(settings, "gate_g3_block_on_instability", False)
    assert _v(stable=True, matches=False).passed_under_policy is False
    assert _v(stable=False, matches=False).passed_under_policy is False


def test_投票无效时两个口径都不放行(monkeypatch):
    """`n=0`（接口抖动导致全部判定无效）不是"未通过"，而是"无法判定" ——
    两个口径都不能把它当放行。"""
    for flag in (True, False):
        monkeypatch.setattr(settings, "gate_g3_block_on_instability", flag)
        assert _v(stable=False, matches=False, n=0).passed_under_policy is False


# ---------------- 离线反算脚本的解析（它就是证据，不能悄悄烂掉）----------------

def test_jsonl_记录能被读出(tmp_path, monkeypatch):
    from eval import g3_stability_ab as ab

    p = tmp_path / "g3_safety_真题单选_zz.jsonl"
    p.write_text(json.dumps({"id": "x1", "n": 2, "judged": ["C"], "stable": False,
                             "passed": False, "expect": ["C"], "trap": "A", "idx": 0})
                 + "\n", encoding="utf-8")
    monkeypatch.setattr(ab, "RES", tmp_path)
    recs, n_tags = ab.load_from_jsonl()
    assert len(recs) == 1 and n_tags == 1
    assert recs[0]["id"] == "x1" and recs[0]["stable"] is False


def test_n0_的记录不计入(tmp_path, monkeypatch):
    """`n=0` 是"无法判定"而非"未通过"，混进来会让分母虚高。"""
    from eval import g3_stability_ab as ab

    p = tmp_path / "g3_safety_真题单选_zz.jsonl"
    p.write_text(json.dumps({"id": "x1", "n": 0, "judged": [], "stable": False,
                             "passed": False, "expect": ["C"]}) + "\n", encoding="utf-8")
    monkeypatch.setattr(ab, "RES", tmp_path)
    recs, _ = ab.load_from_jsonl()
    assert recs == []


def test_md_被拦段落能解析(tmp_path, monkeypatch):
    """只有报告的老分片靠这一段反算 —— 解析写坏会让"释放了几道"直接算错。"""
    from eval import g3_stability_ab as ab

    md = tmp_path / "g3_per_option_真题单选_zz.md"
    md.write_text(
        "| 官方好题（n=51） | **不该** | 0.0 | **0.039** | 越低越安全 |\n"
        "- #2023下-15　期望 ['D']　判定 ['B']　易错项 B　把教辅标注的易错项 B 也判成立（过度判定）\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ab, "RES", tmp_path)
    out = ab.load_from_md(set())
    assert len(out) == 1 and out[0]["n"] == 51
    b = out[0]["blocked"][0]
    assert b["id"] == "2023下-15" and b["judged"] == ["B"] and b["expect"] == ["D"]
