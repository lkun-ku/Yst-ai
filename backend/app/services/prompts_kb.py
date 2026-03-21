"""知识库出题提示词模板与解析（路线② / ③ 共用）。

设计要点：
- **领域无关**：去掉教资特定措辞，题目依据「用户提供的资料切片」生成，适用于任意领域个人资料库
  （与 Q3 决议一致：知识库 = 任意领域）。
- 质量闭环三块提示：**检索相关性评分**、**查询改写**、**生成自检**；外加领域无关**出题提示**。
- 出题提示迁移自 `llm_client.build_doc_question_prompt` 的防幻觉 / 难度具象化 / 去重 / 严格 JSON 经验，
  仅去除教资固化语境，并用 `【生成题目】` 标记便于 FakeLLMClient 分发。
- 解析统一用 `_extract_json`（剥围栏 + 取首尾花括号），与 `llm_client.parse_doc_questions` 同源。
"""

from __future__ import annotations

import json
import re

TYPE_DESC = {
    "single": "单项选择题（4 个选项 A/B/C/D，且只有 1 个正确答案）",
    "multiple": "多项选择题（4 个选项 A/B/C/D，有 2 个或以上正确答案）",
    "judge": "判断题（2 个选项：A 正确 / B 错误）",
    "blank": "填空题（题干中用 ___ 表示空白，answer 给出可接受的答案变体列表）",
    "short": "简答题（answer 给出参考答案文本，explanation 写出评分要点）",
}
DIFF_DESC = {
    "easy": "直接复现资料原文即可作答",
    "medium": "需要归纳资料要点后作答",
    "hard": "需要跨段落综合或推理后作答",
}

#: 布鲁姆认知层级（#26 A）。研究显示 AI 默认约 62% 生成「记忆」层级题目，
#: 而记忆题对备考价值最低；故默认层级池刻意不含 remember，按分布显式约束。
BLOOM_DESC = {
    "remember": "记忆：识别与回忆资料中的事实、定义",
    "understand": "理解：解释、归纳、比较资料要点",
    "apply": "应用：在新情境中使用资料知识解决问题",
    "analyze": "分析：拆解关系、识别证据与推论",
}


def bloom_distribution(count: int, levels: list[str]) -> dict[str, int]:
    """把题量按层级池均分，保证总和 == count（余数分给靠前层级）。"""
    if count <= 0 or not levels:
        return {}
    base, extra = divmod(count, len(levels))
    out: dict[str, int] = {}
    for i, lv in enumerate(levels):
        n = base + (1 if i < extra else 0)
        if n > 0:
            out[lv] = n
    return out


# ---------------- 提示词构建 ----------------

def scope_rewrite_prompt(scope: str, reason: str = "") -> str:
    return (
        "你正在为用户的知识库检索做查询改写。原始查询可能口语化、范围过宽或术语不准。\n"
        f"原始查询：{scope}\n"
        f"检索反馈：{reason or '初次召回的相关切片不足'}\n"
        "请改写为一段更利于向量检索的查询，保留核心主题与关键术语，长度不超过原查询的 1.5 倍。\n"
        "【查询改写】" + scope  # 末尾附原查询，便于 fake 解析回退
    )


def relevance_grade_prompt(scope: str, chunk_content: str) -> str:
    return (
        "判断以下资料切片是否与用户查询相关（相关指：切片内容能支撑就查询主题出题）。\n"
        f"用户查询：{scope}\n"
        f"资料切片：{chunk_content[:800]}\n"
        '只输出 JSON：{"relevant": true/false, "score": 0~1 的相关性分数}\n'
        "【检索相关性评分】"
    )


def kb_question_prompt(
    chunks: list[dict],
    qtype: str,
    count: int,
    difficulty: str = "medium",
    focus: str | None = None,
    existing_stems: list[str] | None = None,
    scope: str | None = None,
    bloom_mix: dict[str, int] | None = None,
) -> str:
    lines = [
        f"你是个人知识库出题助手。请依据下方资料切片，生成 {count} 道{TYPE_DESC.get(qtype, TYPE_DESC['single'])}。",
        "",
        "硬性约束：",
        "1. 只能使用资料切片中出现的信息，禁止引入资料外的知识；资料未涉及的内容不要出。",
        f"2. 难度要求：{DIFF_DESC.get(difficulty, DIFF_DESC['medium'])}。",
        "3. 只输出 JSON，不要任何解释文字或代码块围栏。",
        "4. 每道题必须包含字段：module, knowledge_point, stem, explanation, type。",
    ]
    if qtype in ("single", "multiple", "judge"):
        lines.append("5. 还需包含 options:[{key,text}] 与 answer:[正确选项键]。")
    elif qtype == "blank":
        lines.append("5. 还需包含 answer:[可接受答案文本,...]（含同义 / 别称 / 简写变体）。")
    else:
        lines.append("5. 还需包含 answer: 参考答案文本（explanation 写评分要点）。")
    lines.append('6. module 固定为 "个人资料"；knowledge_point 填该切片所属章节/主题。')
    lines.append(f'7. type 固定为 "{qtype}"。')
    idx = 8
    # 工单 20/W-6：要求模型回传每题所依据的切片编号，实现逐题精确溯源。
    # 用 **切片 id**（全局唯一）而非 seq（跨文档会重复）作为编号来源。
    lines.append(
        f"{idx}. 每道题额外输出字段 source_id：该题所依据的资料切片编号，"
        "取自下方 [切片 #N] 的 N（整数）。若依据多个切片，填最主要的那一个。"
    )
    idx += 1
    if scope:
        lines.append(f"{idx}. 出题范围围绕：{scope}")
        idx += 1
    if focus:
        lines.append(f"{idx}. 侧重要求：{focus}")
        idx += 1
    if bloom_mix:
        # 认知层级显式约束：不指定时模型倾向全出记忆题（#26 A）
        lines.append(f"{idx}. 认知层级分布（布鲁姆）——严格按此比例出题，不要全部出成记忆复述题：")
        for lv, n in bloom_mix.items():
            lines.append(f"   - {n} 道：{BLOOM_DESC.get(lv, lv)}")
        idx += 1
    if existing_stems:
        lines.append(f"{idx}. 禁止与以下已有题干重复或高度相似：")
        # 上限与 doc_max_q_per_task 对齐：长卷（可达 100 题）若只回传最后 20 条题干，
        # 模型看不到更早的已有题目，重复率显著上升，补偿轮余量也会被去重耗尽（#23）。
        for s in (existing_stems or [])[-100:]:
            lines.append(f"   - {s}")
    lines.append("")
    lines.append("资料切片：")
    for c in chunks or []:
        tag = c.get("heading_path") or "未分章"
        # 工单 20/W-6：编号用切片 id（全局唯一），使模型回传的 source_id 无歧义
        lines.append(f"[切片 #{c.get('id')}｜{tag}]")
        lines.append(str(c.get("content") or ""))
        lines.append("")
    lines.append(f'输出格式：{{"questions": [ {{...}}, {{...}} ]}}，共 {count} 道。')
    lines.append("【生成题目】")
    return "\n".join(lines)


def self_check_prompt(
    stem: str,
    options,
    answer,
    explanation: str,
    chunk_content: str,
) -> str:
    opts = json.dumps(options, ensure_ascii=False) if options is not None else "[]"
    ans = json.dumps(answer, ensure_ascii=False) if answer is not None else "[]"
    return (
        "你是出题质量审查员。判断下面这道生成的题目是否忠于所给资料、且格式自洽。\n"
        f"题目题干：{stem}\n选项：{opts}\n答案：{ans}\n解析：{explanation}\n"
        # 截断由调用方按 settings.doc_selfcheck_chars 控制：自检依据必须与生成上下文量级可比，
        # 否则依据后段切片出的题会被必然判为「无依据」（#26）
        f"依据资料：{chunk_content}\n"
        "检查项：①答案是否能在资料中找到依据（无幻觉）；②选项是否互斥且包含正确答案；③解析是否解释正确选项。\n"
        '只输出 JSON：{"passed": true/false, "score": 0~1, "issues": [问题列表]}\n'
        "【生成自检】"
    )


# ---------------- 解析 ----------------

def _extract_json(text: str) -> dict:
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


def parse_relevance(text: str) -> tuple[bool, float]:
    d = _extract_json(text)
    relevant = bool(d.get("relevant", False))
    try:
        score = float(d.get("score", 1.0 if relevant else 0.0))
    except Exception:
        score = 1.0 if relevant else 0.0
    return relevant, max(0.0, min(1.0, score))


def parse_selfcheck(text: str) -> tuple[bool, float, list]:
    d = _extract_json(text)
    passed = bool(d.get("passed", True))
    try:
        score = float(d.get("score", 1.0 if passed else 0.0))
    except Exception:
        score = 1.0 if passed else 0.0
    issues = d.get("issues") or []
    return passed, max(0.0, min(1.0, score)), issues
