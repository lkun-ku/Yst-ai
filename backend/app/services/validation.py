"""题目结构化校验（票 13 / Implementation 19-20）。**按题型分派**，不通过即弃。

重构红线（决策四）：
    官方池路径（single / multiple + 官方五维模块 + 考点归属校验）的行为
    **必须逐字节不变**。新题型（judge / blank / short）走新增分派，
    个人题（module = 个人资料）不校验官方考点骨架。
"""

from __future__ import annotations

import re
from enum import Enum

from ..models import OFFICIAL_MODULES

OFFICIAL_MODULE_VALUES = {m.value for m in OFFICIAL_MODULES}
PERSONAL_MODULE = "个人资料"

SINGLE = "single"
MULTIPLE = "multiple"
JUDGE = "judge"
BLANK = "blank"
SHORT = "short"

#: 通用必备字段（所有题型）
COMMON_REQUIRED = ("module", "knowledge_point", "stem", "explanation")


def _val(v) -> str:
    """把 Enum（含 str Enum）安全转成字符串值。"""
    if isinstance(v, Enum):
        return str(v.value)
    return str(v) if v is not None else ""


_END_PUNCT_RE = re.compile(r"[。！？；,.!?;，、]+$")
_FULL_TO_HALF = (("，", ","), ("。", "."), ("；", ";"), ("！", "!"), ("？", "?"), ("：", ":"), ("、", ","))


def normalize_answer(text: str) -> str:
    """填空判定的文本归一化（B2）：去空白、全角转半角、去句末标点、统一小写。

    用于「填对了却判错」的容错——用户画像对错误的容忍度低，误判会直接击穿信任。
    """
    s = str(text or "")
    for full, half in _FULL_TO_HALF:
        s = s.replace(full, half)
    s = re.sub(r"\s+", "", s)
    s = _END_PUNCT_RE.sub("", s)
    return s.lower()


def _required_keys_for(qtype: str) -> tuple:
    """填空/简答不需要 options（无选项题型）；其余沿用原必备字段。"""
    if qtype in (BLANK, SHORT):
        return COMMON_REQUIRED + ("answer",)
    return COMMON_REQUIRED + ("options", "answer")


def validate_question_payload(d: dict, valid_knowledge_points: set[str]) -> list[str]:
    """校验题目 payload，返回错误列表（空列表 = 通过）。"""
    errors: list[str] = []

    qtype = _val(d.get("type") or SINGLE).strip() or SINGLE

    for k in _required_keys_for(qtype):
        if k not in d:
            errors.append(f"schema: 缺少字段 {k}")
    if errors:
        return errors  # 缺字段时后续检查无意义

    module = _val(d.get("module"))
    if module not in OFFICIAL_MODULE_VALUES and module != PERSONAL_MODULE:
        errors.append(f"schema: 未知模块 {module}")

    # 考点归属：仅官方五维校验。个人题考点来自用户资料，不在官方骨架内。
    if module in OFFICIAL_MODULE_VALUES:
        if d.get("knowledge_point") not in valid_knowledge_points:
            errors.append(f"考点归属不存在：{d.get('knowledge_point')}")

    if not str(d.get("explanation") or "").strip():
        errors.append("解析不能为空")

    errors.extend(_validate_answer_by_type(qtype, d))
    return errors


def _validate_answer_by_type(qtype: str, d: dict) -> list[str]:
    """按题型分派答案/选项校验。"""
    if qtype == SHORT:
        # 简答：参考答案非空即可（不自动判分，评分要点放 explanation）
        ans = d.get("answer")
        text = "".join(str(x) for x in ans).strip() if isinstance(ans, list) else str(ans or "").strip()
        return [] if text else ["参考答案不能为空"]

    if qtype == BLANK:
        # 填空：可接受答案的文本列表（同义 / 别称 / 简写）
        ans = d.get("answer")
        if not isinstance(ans, list) or not ans:
            return ["answer 必须为非空列表"]
        if not all(str(x).strip() for x in ans):
            return ["答案项不能为空"]
        if len({str(x).strip() for x in ans}) != len(ans):
            return ["答案重复（答案唯一校验失败）"]
        return []

    # single / multiple / judge：沿用原选项结构校验，行为不变
    options = d.get("options")
    if not isinstance(options, list) or not options:
        return ["schema: options 必须为非空列表"]

    errors: list[str] = []
    keys = [o.get("key") for o in options if isinstance(o, dict)]
    if len(keys) != len(options):
        errors.append("schema: options 元素须含 key/text")
    if len(set(keys)) != len(keys):
        errors.append("选项键重复（选项互斥校验失败）")
    if not all(str(o.get("text") or "").strip() for o in options):
        errors.append("选项文本不能为空")

    answer = d.get("answer")
    if not isinstance(answer, list) or not answer:
        errors.append("answer 必须为非空列表")
    else:
        if len(set(answer)) != len(answer):
            errors.append("答案重复（答案唯一校验失败）")
        if not set(answer) <= set(keys):
            errors.append(f"答案 {answer} 必须命中选项键 {keys}")

    # 判断题为新增题型，无官方存量数据，可安全加严
    if qtype == JUDGE and len(keys) != 2:
        errors.append("判断题必须且只能有 2 个选项（正确 / 错误）")

    return errors
