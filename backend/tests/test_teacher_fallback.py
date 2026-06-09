"""问答老师的**无据兜底**：查不到材料时不再冷拒答，但必须显式标注「没有出处」。

## 为什么这条要单独钉住

产品口径在 2026-06-15 变了（实测反馈）：问「未成年人保护法里关于学校保护有哪些条文？」时，
检索**确实拿到了**第三十五／四十／四十一条，但模型判 `insufficient`（"只有三条、不够全"）
→ 整条回答变成拒答，用户什么也没得到 —— 而它其实能答。

改法是：**给答案，但明说它没有出处**。这条口径有两个**相反的**失效方向，都不报错：

1. 为了"让拒答率好看"顺手删掉 `insufficient` 检查 → **无据回答被当成有据回答展示**（最严重）；
2. 为了"稳妥"把兜底去掉 → 回到"什么都拒答"，用户又拿不到信息。

所以钉三件事：**拒答变成回答**、**引用必须清空**、**置信必须压到 low**；外加一条：
`plain`（无据对照）模式的口径**不受影响**（那是对照组，本来就不该被兜底改写）。
"""

from __future__ import annotations

import json

from app.services.teacher_agent import _n_answer, _n_refuse


class _StubClient:
    """固定回一段 JSON 的假模型，并记下每次收到的提示词。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def ask(self, prompt, timeout=30):
        self.prompts.append(prompt)
        return json.dumps(self.payload, ensure_ascii=False)


def _insufficient_payload() -> dict:
    """模型判定"材料不足以支撑结论"，并且**顺手塞了一条引用**（要验证它会被清掉）。"""
    return {
        "answer": "资料里只涉及部分条款，不足以完整回答。",
        "citations": [{"quote": "第七条 教师享有下列权利", "source": "教师法"}],
        "confidence": "high",
        "insufficient": True,
    }


def test_模型判不足时降级为无据回答而不是拒答():
    stub = _StubClient(_insufficient_payload())
    state = {
        "question": "未成年人保护法里关于学校保护有哪些条文？",
        "mode": "grounded",
        "client": stub,
        "observations": [{"tool": "search_kb", "ok": True, "items": []}],
    }

    out = _n_answer(state)

    assert out["refused"] is False, "有据答不出应当降级为无据回答，而不是冷拒答"
    assert out["ungrounded"] is True, "必须带上无据标记，前端据此显示不同的标签"
    assert out["notice"], "必须给出提示语，否则用户会误以为这条有出处"


def test_无据回答不许带引用且置信压到low():
    """**这是最要紧的一条**：无据回答若保留了引用，就等于把"模型编的"当成"资料里的"展示。

    模型确实会硬塞引用（本用例的桩就塞了一条）—— 所以不能指望提示词，必须由代码清空。
    """
    stub = _StubClient(_insufficient_payload())
    state = {
        "question": "未成年人保护法里关于学校保护有哪些条文？",
        "mode": "grounded",
        "client": stub,
        "observations": [{"tool": "search_kb", "ok": True, "items": []}],
    }

    ans = _n_answer(state)["answer"]

    assert ans["citations"] == [], "无据回答的引用必须为空（它这次没有材料可引）"
    assert ans["confidence"] == "low", "无据回答的置信必须压到 low，不能自称 high"


def test_兜底用的是另一套措辞():
    """无据时**必须换提示词**：若还留着"材料不足就置 insufficient"，
    模型会**再报一次** insufficient —— 兜底就白做了。所以断言第二段提示词换了口径。
    """
    stub = _StubClient(_insufficient_payload())
    state = {
        "question": "未成年人保护法里关于学校保护有哪些条文？",
        "mode": "grounded",
        "client": stub,
        "observations": [{"tool": "search_kb", "ok": True, "items": []}],
    }

    _n_answer(state)

    assert len(stub.prompts) == 2, "应当发生第二次调用（无据兜底那一次）"
    assert "没有检索到任何资料" in stub.prompts[-1]
    assert "必须是空数组" in stub.prompts[-1]


def test_库里完全没有材料时也降级而不是冷拒答():
    """`_n_check` 判定"无据可依"时不走 answer 节点，而是直接到 refuse 节点 ——
    那条路径同样要兜底（用户口径：**没有的内容接 LLM 回答**）。"""
    stub = _StubClient(_insufficient_payload())
    state = {
        "question": "《民法典》里关于居住权是怎么规定的？",
        "mode": "grounded",
        "client": stub,
        "observations": [],
    }

    out = _n_refuse(state)

    assert out["refused"] is False and out["ungrounded"] is True
    assert out["notice"]


def test_无据对照模式仍按原口径拒答():
    """`plain` 是**对照组**（同一套提示词、唯一差异是有没有检索）。

    它若也被兜底改写成"有答案"，对照实验就失去了意义 —— 这条是防止顺手改坏。
    """
    stub = _StubClient(_insufficient_payload())
    state = {"question": "任意问题", "mode": "plain", "client": stub, "observations": []}

    out = _n_refuse(state)

    assert out["refused"] is True
    assert out["refusal_reason"] == "无据版对照"
    assert stub.prompts == [], "该路径不该调用模型"
