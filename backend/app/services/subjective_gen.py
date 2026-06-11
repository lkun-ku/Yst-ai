"""AI 出主观题 —— RAG for Generation 的**主观题分支**。

## 为什么要有它（2026-06-15 实测核查）

批改页的「从题库抽一道」**必然 404**。逐项放宽过滤条件后确认：题库里主观题 **0 条**
（`type IN (material,writing,short,design)` 且官方池 = 0）。根因是两个既定决策叠加：

- **ADR-0003「真题原文不入库」** → 87 道主观真题只进了答案库，不进题库；
- AI 出题链路（`kb_generate` / `doc_generate`）**只产选择题**。

于是主观题**从来没有被生产过** —— 不是坏了，是这条生产线不存在。本模块补上它。

## 三条纪律

1. **不落库**（第一版）：生成即返回，只服务这一次批改。官方题库保持"只有审校过的题"，
   也不会出现"某人拿到的 AI 题被另一个人抽到"。
2. **有据**：题干由官方语料（考纲/法条/rubric）派生，并把依据一并回传给前端展示 ——
   与出题链路同一条口径（`source_quote` 子串校验的前提是"确实检索到了材料"）。
3. **不得复制真题原文**：提示词显式禁止，材料分析题要求**自拟情境**。
   这条不是客套：真题原文入库是产品红线，而"换个壳的真题"同样越线。
"""

from __future__ import annotations

import json
import re

from ..config import settings
from .scope import NAMESPACE_OFFICIAL, Scope

#: FakeLLMClient 靠这个标记分发（新增链路**必须**同步补替身分支，
#: 否则 fake 模式下这条链路静默返回空，测试会以为"功能没有产出"）。
GENERATION_MARKER = "【生成主观题】"

#: 各题型的**唯一真相**：显示名、卷面分值、检索用的主题词。
#: 分值取自官方卷面（材料分析 14 分/题、写作 50 分），不要凭印象改。
QTYPE_META: dict[str, dict] = {
    "material": {
        "label": "材料分析题",
        "score": 14,
        "query": "素质教育 学生观 教师观 教师职业道德 教育观 材料分析",
    },
    "writing": {
        "label": "写作题",
        "score": 50,
        "query": "教师职业理念 教育观 学生观 立意 写作素材 议论文",
    },
    "short": {
        "label": "简答题",
        "score": 10,
        "query": "教育基础知识 简答 要点 教学原则 德育原则",
    },
    "design": {
        "label": "教学设计题",
        "score": 40,
        "query": "教学设计 教学目标 教学过程 教学评价 语文",
    },
}

#: 题干下限。低于它的多半是模型敷衍（"请谈谈你的看法。"），直接判结构不过。
MIN_STEM_CHARS = 80

#: 回传给前端的依据条数（与批改的依据块同量级即可，前端只用来展示出处）
_MAX_BASIS = 4


def subjective_question_prompt(qtype: str, chunks: list[dict], score: int) -> str:
    """生成主观题的提示词。

    ⚠️ 三个约束缺一不可：**自拟情境**（防真题原文）、**给出作答指令**（否则不是一道能作答的题）、
    **标注依据**（前端要展示"这题依据什么出的"）。
    """
    label = QTYPE_META.get(qtype, {}).get("label", "主观题")
    blocks = "\n".join(
        "[依据 #{}｜{}]\n{}".format(c.get("id"), c.get("heading_path") or "未分章", c.get("content") or "")
        for c in chunks
    )
    return (
        f"{GENERATION_MARKER}\n"
        f"你是教师资格证《综合素质》的命题老师。请依据下方【考纲依据】出**一道{label}**"
        f"（{score} 分）。只输出 JSON，不要解释文字或代码块围栏。\n\n"
        f"【考纲依据】\n{blocks}\n\n"
        "硬要求：\n"
        "1. **必须自拟情境**（自己编一个学校/课堂场景），"
        "**禁止复述任何真题原文**，也不要出现「某年真题」这类字样；\n"
        "2. 材料要具体：有人物、有做法、有可评析的细节（150–400 字）；\n"
        "3. 题目必须带**明确的作答指令**（用「请从……角度评析……」或「要求：……」开头），"
        "让考生知道从哪几个角度答；\n"
        "4. 材料要能对应上【考纲依据】里的理念（题目本质在考那几条理念）。\n\n"
        '输出格式：{"stem": "题干（含材料与作答指令，可含换行）"}\n'
    )


def parse_subjective(text: str | None) -> dict | None:
    """剥围栏 + 取首个 JSON 对象（与 `prompts_kb` / `prompts_teacher` 同源做法）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    try:
        s, e = t.index("{"), t.rindex("}") + 1
        obj = json.loads(t[s:e])
    except (ValueError, json.JSONDecodeError):
        return None
    stem = str(obj.get("stem") or "").strip()
    return {"stem": stem} if stem else None


def structure_issues(stem: str, qtype: str) -> list[str]:
    """G1 结构校验（纯规则，不接 LLM）。

    只挡**结构性缺陷**——"题目好不好"是另一回事（那要靠人审或题目评分），
    但"短得像敷衍""没让考生作答"这两类是硬缺陷，必须在这里拦掉。
    """
    issues: list[str] = []
    if len(stem) < MIN_STEM_CHARS:
        issues.append(f"题干过短（{len(stem)} < {MIN_STEM_CHARS} 字）")
    if not re.search(r"请|要求[:：]|结合材料", stem):
        issues.append("缺少明确的作答指令（请… / 要求：…）")
    if qtype == "material" and len(stem) < 200:
        issues.append("材料分析题的材料过短，不足以支撑评析")
    return issues


def _basis(chunks: list[dict]) -> list[dict]:
    return [
        {"id": c.get("id"), "heading_path": c.get("heading_path"), "content": (c.get("content") or "")[:300]}
        for c in chunks[:_MAX_BASIS]
    ]


def generate_subjective(db, qtype: str, client, k: int = 6) -> dict:
    """检索官方语料 → 让模型出题 → 结构校验。返回**不落库**的题目。

    失败返回带 `error` 的 dict（而不是抛异常）：调用方是"抽一道题"这种可重试的交互，
    抛异常会把 500 抛给用户，而"没生成出来"应当是可解释、可重试的结果。
    """
    meta = QTYPE_META.get(qtype)
    if meta is None:
        return {"error": f"不支持生成的主观题型：{qtype}"}

    from .kb_retrieval import retrieve

    scope = Scope(namespace=NAMESPACE_OFFICIAL)
    try:
        chunks = retrieve(db, meta["query"], scope, k=k)
    except Exception:  # noqa: BLE001 — 检索失败不该让"出题"整体崩，退化为无据生成但如实标注
        chunks = []
    for c in chunks:
        c.setdefault("authority", "官方")

    text = client.ask(subjective_question_prompt(qtype, chunks, meta["score"]))
    parsed = parse_subjective(text)
    if not parsed:
        return {"error": "生成失败（模型输出不符合 JSON 契约），可重试"}

    issues = structure_issues(parsed["stem"], qtype)
    if issues:
        return {"error": "生成的题目未通过结构校验：" + "；".join(issues), "issues": issues}

    return {
        "qtype": qtype,
        "label": meta["label"],
        "score": meta["score"],
        "stem": parsed["stem"],
        "basis": _basis(chunks),
        # 如实标注来源：这是 AI 现出的题，不是题库里的题、更不是真题
        "aigc_flag": True,
        "note": (
            f"本题由 AI 依据考纲现出（{meta['label']}·{meta['score']} 分），"
            "**不是真题**；批改依据来自官方语料与教辅评分规则。"
        ),
    }
