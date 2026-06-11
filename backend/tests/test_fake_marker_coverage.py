"""守卫「约定说有、代码没有」—— **提示词标记与 Fake 分支必须对得上**。

## 为什么需要它

本轮踩到过：`eval/judge.py` 的 docstring 写着「Fake 模式下由 FakeLLMClient 对
「【题目评分】」标记返回确定性分值」，而 `llm_client.py` **根本没有这一支** ——
于是 fake 下 `parse_judge` 五维全部落回默认 `3.0`、`faithfulness_rate` **恒为 0**，
看起来像"事实性全军覆没"，实际是"这一支不存在"。

这类失效**没有任何报错**，也不会被编译守卫抓到（语法是对的），
只能靠"把标记逐个对一遍"。所以把它变成一条测试。

## 规则

1. 仓里**能发出**的每个 `【…】` 标记，必须在 Fake 的 dispatch 里有分支，
   **或**在白名单里**并写明理由** —— 新标记不许被沉默放过；
2. 白名单**不许过期**（里面的标记若其实已经有分支，说明这条规则没在守了）；
3. 任何**声称** Fake 支持某标记的地方（docstring / 注释），该标记必须真的有分支。

## 扫描为什么不算"过度严格"

它只扫**字符串字面量里**的标记，且与 `llm_client.py` 的 dispatch 条件对比 ——
两者都是代码事实，不掺主观判断；判断不了的部分（分节标签 vs 分发依据）
由白名单**显式承担**，而不是靠扫不出来。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_BACKEND = _ROOT
_MARKER = re.compile(r"【([^】\n]{1,20})】")

#: 没有 Fake 分支、但**有理由**的标记。每条都必须写清"为什么不需要分支" ——
#: 白名单不是垃圾桶，它是"规则解释"的落脚点。
_ALLOWED_WITHOUT_BRANCH: dict[str, str] = {
    # —— 提示词内部的**分节标签**：它们只是排版，不承担分发 ——
    "考生作答": "marking 提示词内的分节标签（分发靠「【主观题批改】」）",
    "评分依据": "同上：rubric 块在提示词里的分节标题",
    "观察结果": "teacher 工具循环提示词内的分节标签（分发靠「【答疑作答】」/「【工具决策】」）",
    "采分点": "出题提示词内的分节标签（分发靠「【采分点判定】」）",
    "考纲依据": "subjective_gen 提示词内的分节标签（分发靠「【生成主观题】」）",
    # —— **只在 real 路径**用的构造标记：fake 下走确定性替代，故意不给分支 ——
    "同义改写": "N3 歧义构造（eval/g3_ambiguity.py）：fake 下改用确定性机械替换，不需要模型分支",
    "歧义题构造": "旧 N2/N3 数据集脚本：只在 real 下构造，fake 不参与",
}


def _source_files() -> list[Path]:
    return sorted(
        [p for p in (_BACKEND / "app").rglob("*.py")]
        + [p for p in (_BACKEND / "eval").rglob("*.py")]
    )


def _emitted_markers() -> dict[str, set[str]]:
    """全仓（除 dispatch 实现本身）**能发出**的标记 → 出处文件。"""
    out: dict[str, set[str]] = {}
    for p in _source_files():
        if p.name == "llm_client.py":
            continue  # 它本身就是 dispatch 实现，单独解析
        for m in _MARKER.finditer(p.read_text(encoding="utf-8", errors="ignore")):
            out.setdefault(m.group(1), set()).add(p.name)
    return out


def _fake_branches() -> set[str]:
    """Fake 的 dispatch 条件里出现的标记。"""
    text = (_BACKEND / "app/services/llm_client.py").read_text(encoding="utf-8")
    return set(re.findall(r'if "【([^】]+)】" in prompt', text))


def _claiming_files() -> dict[str, set[str]]:
    """**声称** Fake 支持某标记的地方（同一行里既有 `Fake` 又有标记）。"""
    claims: dict[str, set[str]] = {}
    for p in _source_files():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "Fake" not in line:
                continue
            for m in _MARKER.finditer(line):
                claims.setdefault(m.group(1), set()).add(p.name)
    return claims


def _uncovered(
    emitted: dict[str, set[str]] | set[str],
    branches: set[str],
    allowed: dict[str, str],
) -> set[str]:
    """规则本体（**纯函数**，便于用 fixture 验证它真的会报警）。

    抽出来的原因：守卫若只对着真实文件断言，就没法证明"它在该报警时会报警" ——
    而"永远通过的守卫"比没有守卫更危险。
    """
    keys = set(emitted) if not isinstance(emitted, dict) else set(emitted)
    return {k for k in keys if k not in branches and k not in allowed}


# ---------------- 扫描本身必须真的在扫（防"永远通过"的测试）----------------


def test_扫描确实找得到东西():
    """若扫描正则或路径写坏，下面的断言会全部"通过" —— 那才是最危险的测试。

    所以先钉住扫描的**非空性**与几个已知事实。
    """
    emitted = _emitted_markers()
    branches = _fake_branches()
    assert len(emitted) >= 8, f"只扫到 {len(emitted)} 个标记，扫描多半坏了"
    assert len(branches) >= 8, f"只解析出 {len(branches)} 个 Fake 分支，解析多半坏了"
    assert "生成题目" in branches and "生成题目" in emitted, "已知事实对不上，扫描不可信"


# ---------------- 规则 1：发出的标记要么有分支、要么有理由 ----------------


def test_每个发出的标记都有分支或有理由():
    branches = _fake_branches()
    emitted = _emitted_markers()
    missing = _uncovered(emitted, branches, _ALLOWED_WITHOUT_BRANCH)
    assert not missing, (
        f"这些标记会出现在提示词里、却没有 Fake 分支，也没写理由："
        f"{ {k: sorted(emitted[k]) for k in sorted(missing)} }\n"
        "要么补 `FakeLLMClient` 分支（否则 fake 下会静默走默认值），"
        "要么加进 `_ALLOWED_WITHOUT_BRANCH` 并写明为什么不需要。"
    )


def test_规则本体在被违反时确实会报警():
    """fixture 验证：新标记 + 没有分支 + 不在白名单 → 必须被点出来。"""
    assert _uncovered({"新标记"}, set(), {}) == {"新标记"}
    assert _uncovered({"新标记"}, {"新标记"}, {}) == set()
    assert _uncovered({"新标记"}, set(), {"新标记": "有理由的解释"}) == set()


def test_回归_judge那次失效_题目评分标记必须有分支():
    """**这条钉的就是 2026-06-15 的真实失效**：`eval/judge.py` 的 docstring 声称
    Fake 会处理「【题目评分】」，而 `llm_client.py` 没有这一支 —— 于是 fake 下
    `parse_judge` 五维全落回默认 3.0、`faithfulness_rate` 恒为 0，
    看起来像"事实性全军覆没"。即便将来有人删掉那一支，也会在这里红。"""
    assert "题目评分" in _fake_branches()


# ---------------- 规则 2：白名单不许过期 ----------------


def test_白名单不许过期():
    branches = _fake_branches()
    stale = sorted(set(_ALLOWED_WITHOUT_BRANCH) & branches)
    assert not stale, f"这些标记其实已经有 Fake 分支了，白名单该删：{stale}"


def test_白名单里不许有仓里根本不存在的标记():
    """防止白名单变成"凭印象写的清单" —— 它只该解释**真实存在**的标记。"""
    emitted = _emitted_markers()
    ghosts = sorted(k for k in _ALLOWED_WITHOUT_BRANCH if k not in emitted)
    assert not ghosts, f"白名单收录了仓里不存在（或已改名）的标记：{ghosts}"


def test_白名单的每条都必须给出理由():
    empty = sorted(k for k, why in _ALLOWED_WITHOUT_BRANCH.items() if len(why.strip()) < 8)
    assert not empty, f"这些白名单条目没写理由：{empty}"


# ---------------- 规则 3：声称支持就必须真的有 ----------------


def test_声称Fake支持的标记必须真的有分支():
    """**这一条正是为 judge 那次失效写的**：docstring 说了、代码没有。

    别的规则只能发现"有标记没分支"，这一条还能发现"文字与代码不符" ——
    因为声称的位置（docstring）与实现的位置（dispatch）是两份不同的东西。
    """
    branches = _fake_branches()
    bad = {
        k: sorted(v)
        for k, v in _claiming_files().items()
        if k not in branches and k not in _ALLOWED_WITHOUT_BRANCH
    }
    assert not bad, f"这些地方声称 Fake 支持、但代码里没有分支：{bad}"
