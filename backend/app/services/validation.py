"""题目结构化校验（票 13 / Implementation 19-20）。不通过即弃。"""

REQUIRED_KEYS = ("module", "knowledge_point", "stem", "options", "answer", "explanation")


def validate_question_payload(d: dict, valid_knowledge_points: set[str]) -> list[str]:
    """校验题目 payload，返回错误列表（空列表 = 通过）。

    覆盖：schema 必备字段、选项互斥（键唯一）、答案唯一且必须命中选项、
    解析非空、考点归属存在（以官方考纲考点骨架为准，ADR-0003）。
    """
    errors: list[str] = []

    for k in REQUIRED_KEYS:
        if k not in d:
            errors.append(f"schema: 缺少字段 {k}")
    if errors:
        return errors  # 缺字段时后续检查无意义

    if d["module"] not in {"职业理念", "职业道德", "教育法律法规", "文化素养", "基本能力"}:
        errors.append(f"schema: 未知模块 {d['module']}")

    options = d.get("options")
    if not isinstance(options, list) or not options:
        errors.append("schema: options 必须为非空列表")
    else:
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

    if not str(d.get("explanation") or "").strip():
        errors.append("解析不能为空")

    if d.get("knowledge_point") not in valid_knowledge_points:
        errors.append(f"考点归属不存在：{d.get('knowledge_point')}")

    return errors
