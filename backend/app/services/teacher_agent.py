"""问答老师 Agent：LangGraph StateGraph 编排的**有据答疑**。

流程：`plan →（agent 模式）tool_call → tool_exec →（循环）→ check → answer|refuse → verify → END`

## 三档能力阶梯（同一张图，靠 `mode` 路由）

| mode | 做什么 | 用来回答什么问题 |
| --- | --- | --- |
| `plain` | **不检索**直接作答 | 无据版对照：证明"有据"确实改变了什么 |
| `grounded` | 检索一次 +（**条号类问题**再追加一次确定性结构化定位）→ 带引用作答 | 基础 RAG QA |
| `agent` | 模型自主决定 检索/查条文/自检（循环） | 多跳问题：需要先定位再核对 |

**`grounded` 的"确定性前置"为什么不算把工具交出去**：它由 `find_law_reference` 用正则判断
问题里有没有「X第N条」，**不经过模型决策** —— grounded 仍然是"流程固定、模型不做工具选择"的那一档，
与 `agent`（模型自主决定查什么）的区别完好。加这一步是因为实测发现：条号类问题在 grounded 上
**误拒率 0.1**（2/20 可答题，样本全是「《X》第N条 是怎么规定的」），而同题在 agent 上 0.0 ——
缺的正是这一次结构化定位。见 `tools.find_law_reference` 的 docstring。

**为什么把"无据版"做成模式而不是留一个旧端点**：对照实验要控制变量 ——
同一套提示词、同一个接缝、同一个校验，**唯一差异是有没有检索**。
留一个旧端点会把"模型换代""提示词漂移"一起混进差异里，测出来的东西无法归因。

## 与计划流程图的一处刻意偏离：拒答的判据

计划图写的是「超限或仍不足 → REFUSE」。实现里**拒答只看证据是否存在**
（`check` 节点由观察结果计算），**不看轮次**：

- 超限但**有**材料 → 作答（引用校验会拦住编造）。拒答会让用户白等几轮然后一无所获；
- 未超限但**没**材料 → 拒答。继续循环也只是多烧几次调用去确认"查不到"。

顺带一条更重要的：**sufficiency 由证据算，不由模型自称**。
模型说"够了"只用来**结束循环**，不用来**决定能不能答** ——
否则模型一句"我确定"就能绕过拒答，那正是最该防的。

## 引用校验放在最后，且是**硬**的

`verify` 节点复用 `citation.verify_payloads`（与出题链路同一个判定）。
只要有一条引用**无法在观察到的材料里定位**，整条回答就转拒答 ——
理由是"句句有出处"是产品承诺，带一条编造依据的回答不能因为"其余都对"而放出去。
这条策略会推高拒答率，**而拒答率正是本项要测的指标之一**：
它高说明提示词要收紧，不该靠放松校验来解决。
"""

from __future__ import annotations

import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import settings
from .citation import verify_payloads
from .prompts_teacher import parse_teacher_answer, teacher_answer_prompt
from .tools import (
    ANSWER,
    TOOLS_BY_NAME,
    ToolContext,
    decide,
    execute,
    find_law_reference,
    observe,
)

logger = logging.getLogger(__name__)

MODES = ("plain", "grounded", "agent")
#: 模型不可用时的兜底工具：至少把材料找回来，别让整个问答因为"决策失败"而失败
FALLBACK_TOOL = "search_kb"


class TeacherState(TypedDict, total=False):
    db: object
    client: object
    candidate_id: int
    question: str
    mode: str
    scope: object
    embed_fn: object
    max_calls: int
    observations: list
    pending: dict
    calls: int
    sufficient: bool
    answer: dict
    refused: bool
    refusal_reason: str
    #: 无据兜底：回答来自模型通识而非检索材料（前端据此显示提示条）
    ungrounded: bool
    notice: str
    citation: dict
    on_event: object


def _emit(s: TeacherState, type_: str, text: str, detail=None) -> None:
    fn = s.get("on_event")
    if fn:
        fn(type_, text, detail)


def _evidence_chunks(s: TeacherState) -> list[dict]:
    """所有观察里能作为依据的切片（只有带 content 的才是依据）。

    **按 `id` 去重，保留首次出现**。同一个切片确实会被两个途径分别取到：
    `search_kb` 检索到它、条号类问题又会被结构化定位精确命中它
    （加了确定性前置之后这是常态，不再是罕见情况）。
    不去重会让前端「依据 N 处」虚高 —— 那是**用户直接看到的数字**，虚一个都算不准。

    保留首次出现是够用的：出现重复本身就意味着它被检索到了（带 `keyword_score`），
    判定 `_sufficient` 时走检索侧那套本来也会通过；而真正的误拒场景
    （检索没召回、只有结构化定位命中）**没有重复**，那时留下的正是"无 keyword_score"的那一份。
    """
    out: list[dict] = []
    seen: set = set()
    for ob in s.get("observations") or []:
        for it in ob.get("items") or []:
            if not (isinstance(it, dict) and it.get("content")):
                continue
            key = it.get("id")
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            out.append(it)
    return out


def _run_tool(s: TeacherState, name: str, args: dict) -> dict:
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        # 模型选了一个不存在的工具 → 当成失败观察，让它自己看到并改口
        return {"ok": False, "error": f"未知工具 {name}", "items": [], "text": ""}
    ctx = ToolContext(s["db"], s["scope"], s["candidate_id"], s.get("embed_fn"))
    return execute(spec, args, ctx)


# ---------------- 图节点 ----------------

def _n_plan(s: TeacherState) -> dict:
    """准备阶段：`grounded` 在这里直接检索一次（不做决策），`plain` 什么都不查。"""
    mode = s.get("mode") or "grounded"
    if mode == "plain":
        _emit(s, "plan", "无据版对照：跳过检索，直接作答")
        return {"observations": [], "calls": 0}
    if mode == "grounded":
        res = _run_tool(s, FALLBACK_TOOL, {"query": s["question"]})
        _emit(s, "retrieve", f"检索完成（{'成功' if res.get('ok') else '失败'}）")
        obs = [observe(FALLBACK_TOOL, res)]
        calls = 1
        # 确定性前置：问题里出现「X第N条」就直接走结构化定位（不经过模型决策）。
        # 只靠 search_kb 时，条号类问题会召回一堆没回答它的材料 → 模型**正确地**判
        # insufficient → 误拒。实测：grounded 误拒率 0.1、agent（可调 lookup_law）0.0。
        ref = find_law_reference(s["question"])
        if ref:
            res2 = _run_tool(s, "lookup_law", ref)
            if res2.get("items"):
                obs.append(observe("lookup_law", res2))
                calls += 1
                _emit(s, "tool", f"结构化定位 {ref['law']}{ref['article']}")
            else:
                # 库里没有该条号：**别静默吞掉**。它不改变判定（无证据、不引入误答），
                # 但排查时要能看见"这一步跑过了、只是没命中"。
                _emit(s, "observation", f"lookup_law 未命中 {ref['law']}{ref['article']}")
        return {"observations": obs, "calls": calls}
    _emit(s, "plan", "Agent 模式：由模型决定查什么")
    return {"observations": [], "calls": 0}


def _route_after_plan(s: TeacherState) -> str:
    mode = s.get("mode") or "grounded"
    if mode == "plain":
        return "answer"  # 无据版就是要"不查也答"，这是它的定义
    if mode == "agent":
        return "tool_call"
    return "check"


def _n_tool_call(s: TeacherState) -> dict:
    """让模型选下一步。模型不可用/解析失败 → 兜底检索一次（不阻断链路）。"""
    max_calls = s.get("max_calls") or settings.agent_max_tool_calls
    decision = decide(s["client"], s["question"], s.get("observations"), max_calls)

    if decision is None:
        # 决策失败不等于问答失败：用兜底工具把材料找回来，下一轮再让模型作答
        if any(ob.get("tool") == FALLBACK_TOOL for ob in s.get("observations") or []):
            return {"sufficient": True, "pending": {}}  # 已经兜底过 → 别重复查
        _emit(s, "tool", "决策不可用，改用兜底检索")
        return {"pending": {"tool": FALLBACK_TOOL, "args": {"query": s["question"]}, "reason": "兜底"}}

    name = decision["tool"]
    if name == ANSWER:
        return {"pending": {}}
    _emit(s, "tool", f"调用 {name}：{decision.get('reason') or ''}")
    return {"pending": decision}


def _route_after_tool_call(s: TeacherState) -> str:
    return "tool_exec" if s.get("pending") else "check"


def _n_tool_exec(s: TeacherState) -> dict:
    pending = s.get("pending") or {}
    res = _run_tool(s, pending.get("tool") or "", pending.get("args") or {})
    obs = list(s.get("observations") or []) + [observe(pending.get("tool") or "", res)]
    _emit(
        s,
        "observation",
        f"{pending.get('tool')} → {'成功' if res.get('ok') else '失败：' + str(res.get('error'))}",
        {"items": len(res.get("items") or [])},
    )
    return {"observations": obs, "calls": s.get("calls", 0) + 1, "pending": {}}


def _route_after_tool_exec(s: TeacherState) -> str:
    max_calls = s.get("max_calls") or settings.agent_max_tool_calls
    return "check" if s.get("calls", 0) >= max_calls else "tool_call"


def _sufficient(chunks: list[dict]) -> bool:
    """有据可答的判据 = 有材料 **且** 材料不是「什么都召回一点」的产物。

    **为什么不能只看"有没有材料"**：检索**永远**会返回 top-k ——
    库里什么都没匹配上时，RRF 也会靠兜底路径硬凑出几条。于是"有材料"几乎恒真，
    对库外问题（如问《民法典》而库里只有教育法条）也会自信作答。
    这是实测出来的：只数材料时，**正确拒答率恒为 0**（`eval/teacher_eval.py`）。

    两类材料区别对待：

    | 来源 | 判据 | 理由 |
    | --- | --- | --- |
    | 结构化定位（`lookup_law`） | **直接算有据** | 按法名+条号精确命中，本身就是精确证据 |
    | 检索（`search_kb`） | 须过**相关性下限** | 有关键词命中、或精排给正分；否则只是"凑数的 top-k" |

    ⚠️ **这是词形层面的下限，不是语义相关性判定**：它挡得住"关键实体在库里根本不存在"，
    挡不住"实体存在但问的是另一个方面"。语义级判定应复用
    `kb_generate._grade_and_filter` 的相关性评分 —— 本项未做，已记入 ADR-0017 的已知边界。
    """
    if not chunks:
        return False
    retrieval_items = [c for c in chunks if "keyword_score" in c]
    if len(retrieval_items) < len(chunks):
        return True  # 至少有一条来自结构化定位 → 精确证据
    if not retrieval_items:
        return True
    return any(
        (c.get("keyword_score") or 0) > 0 or (c.get("rerank_score") or -1) > 0
        for c in retrieval_items
    )


def _n_check(s: TeacherState) -> dict:
    """**由证据算**是否可答（不看模型自称，也不看轮次）。"""
    chunks = _evidence_chunks(s)
    sufficient = _sufficient(chunks)
    _emit(s, "check", f"可用依据 {len(chunks)} 条 → {'可以作答' if sufficient else '无据可依'}")
    return {"sufficient": sufficient}


def _route_after_check(s: TeacherState) -> str:
    return "answer" if s.get("sufficient") else "refuse"


#: 无据兜底时的提示语 —— 必须**显式**，否则用户会把它当成有出处的答案。
_UNGROUNDED_NOTICE = (
    "资料库里没有找到能直接回答这个问题的依据。以下是模型的通识性回答，"
    "未经过资料佐证，请以官方教材／法条原文为准。"
)


def _ungrounded_answer(s: TeacherState) -> dict | None:
    """**无据兜底**：不检索、不带引用，直接让模型用通识作答。

    为什么要有它（2026-06-15 实测反馈）：问「未成年人保护法里关于学校保护有哪些条文？」
    时，检索**确实拿到了**第三十五／四十／四十一条，但模型判 `insufficient`
    （因为"只有三条、不够全"）→ 整条回答变成拒答。用户什么也没得到，而它其实能答。

    分寸：**降级 ≠ 放松**。无据回答必须带 `_UNGROUNDED_NOTICE` 标注、`citations` 恒为空、
    `confidence` 强制 low。产品承诺从「宁可拒答，也不给没有出处的答案」改为
    「**给答案，但明说它没有出处**」—— 前者让人拿不到信息，后者让人自己判断信不信。
    """
    text = s["client"].ask(teacher_answer_prompt(s["question"], [], ungrounded=True))
    parsed = parse_teacher_answer(text) if text else None
    if not parsed:
        return None
    # 无据回答**不许带引用**：模型若硬塞引用就清掉（它这次根本没有材料可引）。
    parsed["citations"] = []
    parsed["confidence"] = "low"
    return parsed


def _n_answer(s: TeacherState) -> dict:
    text = s["client"].ask(teacher_answer_prompt(s["question"], s.get("observations")))
    parsed = parse_teacher_answer(text) if text else None
    if not parsed:
        # 解析不出来 → 转拒答。**不编一个回答**：那是本产品最不能犯的错。
        return {
            "refused": True,
            "refusal_reason": "生成的回答无法解析（模型输出不符合 JSON 契约）",
            "answer": None,
        }
    if parsed.get("insufficient") and s.get("mode") != "plain":
        fb = _ungrounded_answer(s)
        if fb:
            _emit(s, "fallback", "有据答不出 → 已降级为无据回答（明确标注）")
            return {"answer": fb, "refused": False, "ungrounded": True, "notice": _UNGROUNDED_NOTICE}
        return {
            "refused": True,
            "refusal_reason": "材料不足以支撑结论（模型判定 insufficient）",
            "answer": parsed,
        }
    return {"answer": parsed, "refused": False}


def _route_after_answer(s: TeacherState) -> str:
    """已拒答就别再跑引用校验 —— 那会打出一条全零的"引用校验"事件，
    让人以为校验跑了并且没什么问题，实际是根本没有回答可校验。"""
    return "end" if s.get("refused") else "verify"


def _n_refuse(s: TeacherState) -> dict:
    if s.get("refusal_reason"):
        return {}
    if s.get("mode") != "plain":
        # 「库里没有相关材料」也走无据兜底而不是冷拒答（用户口径：没有的内容接 LLM 回答）。
        fb = _ungrounded_answer(s)
        if fb:
            _emit(s, "fallback", "库里无相关材料 → 已降级为无据回答（明确标注）")
            return {"answer": fb, "refused": False, "ungrounded": True, "notice": _UNGROUNDED_NOTICE}
    reason = "知识库里没有可支撑这个问题的材料" if s.get("mode") != "plain" else "无据版对照"
    return {"refused": True, "refusal_reason": reason, "answer": None}


def _n_verify(s: TeacherState) -> dict:
    """引用硬校验（与出题链路同一个 `citation.verify_payloads`）。"""
    ans = s.get("answer") or {}
    quotes = [c.get("quote") for c in ans.get("citations") or []]
    chunks = _evidence_chunks(s)
    report = verify_payloads([{"source_quotes": quotes}], chunks, settings.citation_min_quote_chars)
    row = report.as_row()
    _emit(s, "citation", f"引用校验 · 逐字 {row['exact']} · 归一 {row['normalized']} · 无法定位 {row['fabricated']}")

    if report.n_fabricated > 0:
        # 有一条依据无法定位 → 整条回答转拒答。"句句有出处"不能因为"其余都对"打折。
        return {
            "refused": True,
            "refusal_reason": f"{report.n_fabricated} 条引用无法在材料中定位（疑似编造）",
            "citation": row,
        }
    return {"citation": row}


# ---------------- 图构建 ----------------

def _build_graph():
    g = StateGraph(TeacherState)
    g.add_node("plan", _n_plan)
    g.add_node("tool_call", _n_tool_call)
    g.add_node("tool_exec", _n_tool_exec)
    g.add_node("check", _n_check)
    g.add_node("answer", _n_answer)
    g.add_node("refuse", _n_refuse)
    g.add_node("verify", _n_verify)

    g.add_edge(START, "plan")
    g.add_conditional_edges(
        "plan", _route_after_plan, {"answer": "answer", "check": "check", "tool_call": "tool_call"}
    )
    g.add_conditional_edges(
        "tool_call", _route_after_tool_call, {"tool_exec": "tool_exec", "check": "check"}
    )
    g.add_conditional_edges(
        "tool_exec", _route_after_tool_exec, {"tool_call": "tool_call", "check": "check"}
    )
    g.add_conditional_edges("check", _route_after_check, {"answer": "answer", "refuse": "refuse"})
    g.add_conditional_edges("answer", _route_after_answer, {"verify": "verify", "end": END})
    g.add_edge("verify", END)
    g.add_edge("refuse", END)
    return g.compile()


_GRAPH = _build_graph()


def ask(
    db,
    candidate_id: int,
    question: str,
    scope,
    mode: str = "grounded",
    embed_fn=None,
    client=None,
    max_calls: int | None = None,
    on_event=None,
) -> dict:
    """问答老师主入口。**不抛异常**：任何降级都表达在返回值里。

    返回：
        {
          "mode", "answer", "citations", "confidence",
          "refused", "refusal_reason",
          "observations": [{"tool", "ok", "error", "items"}],  # items 已剥离（体积大）
          "evidence": [切片],          # 供前端"依据 N 处"展示
          "tool_calls": int,
          "citation": {...},           # 引用校验计数
        }

    `mode` 见模块 docstring；`on_event(type, text, detail)` 供前端渲染时间线。
    """
    from .llm_client import get_llm_client

    mode = mode if mode in MODES else "grounded"
    state: TeacherState = {
        "db": db,
        "client": client or get_llm_client(),
        "candidate_id": candidate_id,
        "question": question,
        "mode": mode,
        "scope": scope,
        "embed_fn": embed_fn,
        "max_calls": max_calls if max_calls is not None else settings.agent_max_tool_calls,
        "observations": [],
        "calls": 0,
        "on_event": on_event,
    }
    result = _GRAPH.invoke(state, {"recursion_limit": 25})

    ans = result.get("answer") or {}
    observations = result.get("observations") or []
    return {
        "mode": mode,
        "answer": ans.get("answer") if not result.get("refused") else None,
        "citations": (ans.get("citations") or []) if not result.get("refused") else [],
        "confidence": ans.get("confidence") or ("low" if result.get("refused") else "medium"),
        "refused": bool(result.get("refused")),
        "refusal_reason": result.get("refusal_reason") or "",
        # 无据兜底标记：前端据此显示「通识性回答、未经资料佐证」提示条
        "ungrounded": bool(result.get("ungrounded")),
        "notice": result.get("notice") or "",
        "observations": [
            {"tool": ob.get("tool"), "ok": ob.get("ok"), "error": ob.get("error")} for ob in observations
        ],
        "evidence": _evidence_chunks(result),
        "tool_calls": result.get("calls", 0),
        "citation": result.get("citation") or {},
    }
