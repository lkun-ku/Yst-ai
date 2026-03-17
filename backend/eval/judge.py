"""LLM-as-judge 出题质量评分（五维 rubric）。

五维：factuality 事实性(忠于资料无幻觉) / coverage 考点覆盖 /
uniqueness 答案唯一性·干扰度 / explanation 解析质量 / difficulty 难度达标。
每维 1~5 分。

Fake 模式下由 FakeLLMClient 对「【题目评分】」标记返回确定性分值；Real 模式下由
真实 LLM 按 rubric 打分。返回每题各维分数字典列表。
"""

from __future__ import annotations

import json

from app.services.prompts_kb import _extract_json

JUDGE_DIMS = ["factuality", "coverage", "uniqueness", "explanation", "difficulty"]


def judge_prompt(q: dict, ctx: str) -> str:
    opts = json.dumps(q.get("options"), ensure_ascii=False) if q.get("options") else "[]"
    ans = json.dumps(q.get("answer"), ensure_ascii=False) if q.get("answer") else "[]"
    return (
        "你是出题质量评审。请就下面这道题目，依据所给资料，按 5 个维度各打 1~5 分（整数）。\n"
        f"题干：{q.get('stem')}\n选项：{opts}\n答案：{ans}\n解析：{q.get('explanation')}\n"
        f"依据资料：{ctx[:1200]}\n"
        "维度：factuality 事实性(忠于资料无幻觉)；coverage 考点覆盖；"
        "uniqueness 答案唯一性/干扰度；explanation 解析质量；difficulty 难度达标。\n"
        '只输出 JSON：{"factuality":n,"coverage":n,"uniqueness":n,"explanation":n,"difficulty":n}\n'
        "【题目评分】"
    )


def parse_judge(text: str) -> dict:
    d = _extract_json(text)
    out = {}
    for dim in JUDGE_DIMS:
        try:
            out[dim] = float(d.get(dim, 3))
        except Exception:
            out[dim] = 3.0
    return out


def judge_questions(client, questions: list[dict], ctx: str) -> list[dict]:
    """对一批题目逐题评分，返回与 questions 对齐的分数字典列表。"""
    return [parse_judge(client.ask(judge_prompt(q, ctx))) for q in questions]


def avg_scores(scores: list[dict]) -> dict:
    if not scores:
        return {d: 0.0 for d in JUDGE_DIMS}
    return {d: round(sum(s[d] for s in scores) / len(scores), 3) for d in JUDGE_DIMS}
