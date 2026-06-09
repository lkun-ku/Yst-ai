"""问答老师的提示词与解析（**必须与 `citation.py` 的口径对齐**）。

两条硬要求决定了这里的措辞：

1. **引用必须原样摘录**。`citation.verify_quote` 是子串判定，改写过就等于编造 ——
   所以提示词必须把"逐字"讲清楚，并要求引用长度足够（过短会被 `min_quote_chars` 拦下）。
2. **材料不足要显式说**。拒答是**一等结果**，不是失败。
   若提示词把"答不出来"当耻辱，模型就会硬编 —— 而硬编恰好是本产品最不能犯的错。

因此这里的每个 JSON 契约都带一个**显式的"不足"出口**：
决策里有 `answer`（不再查了）与 `insufficient`，作答里有 `insufficient`。
让"我查不到"成为模型可以合法选择的动作，比在生成后拦截它更有效。
"""

from __future__ import annotations

import json
import re

from .citation import DEFAULT_MIN_QUOTE_CHARS

#: 供 Fake 实现分发的标记（与 prompts_kb 的 【生成题目】 同一套做法）
MARK_TOOL_DECISION = "【工具决策】"
MARK_ANSWER = "【答疑作答】"

TYPE_DESC = {
    "definition": "概念定义",
    "comparison": "易混辨析",
    "law": "法条与规定",
    "application": "情境应用",
    "other": "其他",
}


def _observations_block(observations: list[dict] | None) -> str:
    """把工具观察拼成给模型看的一段。编号稳定，便于引用时指认。"""
    if not observations:
        return "（还没有任何检索结果）"
    lines: list[str] = []
    for i, ob in enumerate(observations, 1):
        head = f"[观察 {i}｜{ob.get('tool')}｜{'成功' if ob.get('ok') else '失败'}"
        if not ob.get("ok"):
            head += f"｜{ob.get('error') or '未知原因'}"
        lines.append(head + "]")
        text = str(ob.get("text") or "").strip()
        lines.append(text or "（空）")
        lines.append("")
    return "\n".join(lines)


def tool_decision_prompt(
    question: str,
    observations: list[dict] | None,
    tool_blocks: str,
    max_calls: int,
) -> str:
    """让模型决定下一步：调哪个工具，还是直接作答。"""
    used = len(observations or [])
    return (
        "你是教资备考问答老师的**检索调度器**。判断为了回答问题还需要查什么，"
        "然后只输出一个 JSON 决策，不要任何解释文字或代码块围栏。\n\n"
        f"用户问题：{question}\n\n"
        f"可用工具：\n{tool_blocks}\n\n"
        f"已用轮次：{used}/{max_calls}\n\n"
        f"已有的观察结果：\n{_observations_block(observations)}\n"
        "决策要求：\n"
        '1. 若已有观察结果足以作答，输出 {"tool": "answer"}；不要为了「更全」而无谓地多查。\n'
        "2. 问题里出现具体条款号（如「教师法第七条」）时优先用 lookup_law，它比检索准。\n"
        '3. 若已用轮次达到上限，只能输出 {"tool": "answer"}。\n'
        '4. 需要调用工具时输出：{"tool": "工具名", "args": {参数对象}, "reason": "一句话理由"}\n'
        f"{MARK_TOOL_DECISION}"
    )


def teacher_answer_prompt(
    question: str, observations: list[dict] | None, ungrounded: bool = False
) -> str:
    """依据观察结果作答，**强制带引用**。

    `ungrounded=True` 是**无据兜底**（2026-06-15 实测反馈）：检索确实找不到材料时，
    不再冷拒答，而是明确告诉模型"这次没有材料"，让它用通识作答并**自报未经佐证**。

    ⚠️ **两种情况必须用不同的措辞**：有据时 `insufficient` 是合法出口（防硬编，这条不能松）；
    但无据时若还留着"材料不足就置 true"，模型会**再报一次 insufficient** —— 兜底就白做了。
    """
    if ungrounded:
        return (
            "你是教资备考问答老师。**本次没有检索到任何资料**（资料库无相关内容）。"
            "只输出 JSON，不要解释文字或代码块围栏。\n\n"
            f"用户问题：{question}\n\n"
            "输出字段：\n"
            "1. answer：用你掌握的知识作答（口语、分点、不要客套话）。"
            "**第一句必须写明**：「资料库中没有找到对应依据，以下是通识性回答，请以官方教材为准」。\n"
            "2. citations：**必须是空数组 []** —— 没有资料可引，"
            "**禁止编造任何「原文引用」或条文原文**。\n"
            "3. confidence：只能是 low 或 medium。\n"
            "4. insufficient：固定 false（已经明确标注无依据，不需要再拒答）。\n"
            "⚠️ **不要给出具体条号的确切原文**（那是编造）；"
            "只能说到「《X 法》中有相关规定」这个粒度，并提示考生以法条原文/官方教材为准。\n"
            '输出格式：{"answer": "...", "citations": [], "confidence": "low", "insufficient": false}\n'
            f"{MARK_ANSWER}"
        )
    return (
        "你是教资备考问答老师。只能依据下方【观察结果】中的材料回答，"
        "禁止引入材料之外的知识。只输出 JSON，不要解释文字或代码块围栏。\n\n"
        f"用户问题：{question}\n\n"
        f"【观察结果】\n{_observations_block(observations)}\n"
        "输出字段：\n"
        "1. answer：面向考生的回答（口语、分点、不要客套话）。\n"
        '2. citations：数组，每项 {"quote": "原文片段", "source": "出处"}。\n'
        "   - quote 必须是观察结果里**一字不差**的连续原文，长度不少于 "
        f"{DEFAULT_MIN_QUOTE_CHARS} 字、不超过 60 字；\n"
        "   - 禁止改写、概括、合并句子，禁止自己补书名号；\n"
        "   - 程序会逐字比对，改写会导致整条回答作废。\n"
        "3. confidence：high / medium / low。\n"
        "4. insufficient：布尔值。**只有材料与问题完全无关、毫无可用内容时才为 true**。\n"
        "   ⚠️ **材料只覆盖问题的一部分时，必须先基于已有材料作答**，再在 answer 末尾"
        "明确点出「资料里只涉及……，未覆盖……」（例如问「学校保护有哪些条文」而材料只有三条，"
        "就把这三条答出来并说明这是资料库中涉及的部分）。"
        "整体拒答只留给「材料与问题毫无关系」这一种情形 —— 宁可给**带说明的部分答案**，"
        "也不要因为「不够全」而拒答。\n"
        '输出格式：{"answer": "...", "citations": [...], '
        '"confidence": "high", "insufficient": false}\n'
        f"{MARK_ANSWER}"
    )


# ---------------- 解析 ----------------

def _extract_json(text: str) -> dict:
    """剥围栏 + 取首尾花括号（与 prompts_kb 同源）。"""
    if not text:
        return {}
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    try:
        s = t.index("{")
        e = t.rindex("}") + 1
        return json.loads(t[s:e])
    except Exception:
        return {}


def parse_tool_decision(text: str) -> dict | None:
    """解析工具决策。**无法解析时返回 None**（调用方按"直接作答"处理）。

    为什么不抛异常：模型偶尔输出非 JSON 是**正常现象**，不是故障。
    把它当成故障会让整条问答链路失败，而正确答案往往是我们已有的材料就够了。
    """
    d = _extract_json(text)
    if not d:
        return None
    tool = str(d.get("tool") or "").strip()
    if not tool:
        return None
    args = d.get("args")
    return {
        "tool": tool,
        "args": args if isinstance(args, dict) else {},
        "reason": str(d.get("reason") or "")[:200],
    }


def parse_teacher_answer(text: str) -> dict | None:
    """解析作答。无法解析时返回 None（调用方转拒答，而不是编一个回答）。"""
    d = _extract_json(text)
    if not d:
        return None
    answer = str(d.get("answer") or "").strip()
    if not answer:
        return None
    citations: list[dict] = []
    for c in d.get("citations") or []:
        if isinstance(c, dict):
            quotes = c.get("quote")
            if isinstance(quotes, list):  # 模型偶尔给数组
                for q in quotes:
                    citations.append({"quote": str(q), "source": str(c.get("source") or "")})
            else:
                citations.append({"quote": str(quotes or ""), "source": str(c.get("source") or "")})
        elif isinstance(c, str):
            citations.append({"quote": c, "source": ""})
    conf = str(d.get("confidence") or "medium").strip().lower()
    return {
        "answer": answer,
        "citations": citations,
        "confidence": conf if conf in ("high", "medium", "low") else "medium",
        "insufficient": bool(d.get("insufficient")),
    }
