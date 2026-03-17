"""接缝 B：大模型客户端封装（服务层唯一 AI 出口，Implementation 3）。

- FakeLLMClient：测试用假实现，不消耗任何 API 额度（测试决策 33/45）。
- RealLLMClient：OpenAI 兼容 Chat Completions 真实实现（LLM_MODE=real 启用）。
- `kind="doc_question"`：文档出题，按题型生成，输出结构化题目列表。

枚举值说明：variant 产物要求结构化 payload（进入管线前仍过结构化校验，不通过即弃）。
"""

import json
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import settings


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


@dataclass
class GenerationRequest:
    kind: str  # "variant" | "review_paragraph" | "doc_question"
    knowledge_point: str
    context: dict | None = None


@dataclass
class GenerationResult:
    text: str
    source: str = "realtime"
    payload: dict | None = field(default=None)  # variant 的结构化题目产物
    payloads: list | None = field(default=None)  # doc_question：题目列表


def _variant_payload(kp: str, module: str, variant_no: int) -> dict:
    """构造一份可通过结构化校验的变式题 payload（fake 实现用）。"""
    return {
        "module": module,
        "knowledge_point": kp,
        "stem": f"（实时变式{variant_no}）下列关于《{kp}》的表述，正确的是？",
        "options": [
            {"key": "A", "text": f"《{kp}》的正确表述（实时变式{variant_no}）"},
            {"key": "B", "text": f"《{kp}》的常见误解（实时变式{variant_no}）"},
            {"key": "C", "text": f"与《{kp}》无关的表述（实时变式{variant_no}）"},
            {"key": "D", "text": f"对《{kp}》的颠倒表述（实时变式{variant_no}）"},
        ],
        "answer": ["A"],
        "explanation": f"本题考查《{kp}》：正确选项 A 为该考点的核心要点表述。",
        "type": "single",
    }


def _fake_stem(kp: str, qtype: str, i: int) -> str:
    if qtype == "judge":
        return f"（资料变式{i}）以下关于《{kp}》的表述是否正确？"
    if qtype == "blank":
        return f"（资料变式{i}）《{kp}》的核心要点是____。"
    if qtype == "short":
        return f"（资料变式{i}）简述《{kp}》的主要内容。"
    return f"（资料变式{i}）关于《{kp}》的表述，正确的是？"


def _fake_doc_questions(kp: str, qtype: str, count: int, existing_stems: list[str] | None = None) -> list[dict]:
    """fake 模式的文档出题产物：结构与真实产物一致，便于管线端到端跑通。

    会跳过 `existing_stems` 里已有的题干，模拟"模型遵守去重指令"的行为，
    否则 fake 模式下每批都从"变式1"编号，会被下游去重大量丢弃，导致出题数少于目标数。
    """
    existing = set(existing_stems or ())
    out: list[dict] = []
    i = 1
    guard = 0
    limit = count + len(existing) + 500

    while len(out) < max(0, count) and guard < limit:
        guard += 1
        stem = _fake_stem(kp, qtype, i)
        if stem in existing:
            i += 1
            continue
        existing.add(stem)

        item = {
            "module": "个人资料",
            "knowledge_point": kp,
            "stem": stem,
            "explanation": f"依据资料片段，《{kp}》的相关表述如解析所示。",
            "type": qtype,
        }
        if qtype == "judge":
            item["options"] = [{"key": "A", "text": "正确"}, {"key": "B", "text": "错误"}]
            item["answer"] = ["A"]
        elif qtype == "blank":
            item["answer"] = ["核心要点", "要点"]
        elif qtype == "short":
            item["answer"] = f"参考答案：见资料中《{kp}》相关片段。"
            item["explanation"] = "评分要点：要点完整、表述准确、结合资料原文。"
        elif qtype == "multiple":
            item["options"] = [
                {"key": "A", "text": f"《{kp}》的正确表述{i}"},
                {"key": "B", "text": f"《{kp}》的另一正确表述{i}"},
                {"key": "C", "text": f"与《{kp}》无关的表述{i}"},
                {"key": "D", "text": f"对《{kp}》的颠倒表述{i}"},
            ]
            item["answer"] = ["A", "B"]
        else:  # single
            item["options"] = [
                {"key": "A", "text": f"《{kp}》的正确表述{i}"},
                {"key": "B", "text": f"《{kp}》的常见误解{i}"},
                {"key": "C", "text": f"与《{kp}》无关的表述{i}"},
                {"key": "D", "text": f"对《{kp}》的颠倒表述{i}"},
            ]
            item["answer"] = ["A"]
        out.append(item)
        i += 1
    return out


def build_doc_question_prompt(
    chunks: list[dict],
    qtype: str,
    count: int,
    difficulty: str = "medium",
    focus: str | None = None,
    existing_stems: list[str] | None = None,
) -> str:
    """文档出题提示词：防幻觉 + 难度具象化 + 去重 + 严格 JSON。"""
    lines = [
        f"你是教资考试出题助手。请依据下方资料生成 {count} 道{TYPE_DESC.get(qtype, TYPE_DESC['single'])}。",
        "",
        "硬性约束：",
        "1. 只能使用资料中出现的信息，禁止引入外部知识；资料未涉及的内容不要出。",
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
    lines.append('6. module 固定为 "个人资料"；knowledge_point 填该片段所属章节标题。')
    lines.append(f'7. type 固定为 "{qtype}"。')
    idx = 8
    if focus:
        lines.append(f"{idx}. 侧重要求：{focus}")
        idx += 1
    if existing_stems:
        lines.append(f"{idx}. 禁止与以下已有题干重复或高度相似：")
        for s in (existing_stems or [])[-20:]:
            lines.append(f"   - {s}")
    lines.append("")
    lines.append("资料片段：")
    for c in chunks or []:
        tag = c.get("heading_path") or "未分章"
        lines.append(f"[资料片段 {c.get('seq')}｜{tag}]")
        lines.append(str(c.get("content") or ""))
        lines.append("")
    lines.append(f'输出格式：{{"questions": [ {{...}}, {{...}} ]}}，共 {count} 道。')
    return "\n".join(lines)


def parse_doc_questions(content: str) -> list[dict]:
    """三层保障的第二层：本地 JSON 修复（剥围栏、取首尾花括号）。"""
    if not content:
        return []
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        data = json.loads(content[start:end])
    except Exception:
        return []
    if isinstance(data, dict):
        qs = data.get("questions")
        return qs if isinstance(qs, list) else []
    if isinstance(data, list):
        return data
    return []


class LLMClient(ABC):
    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...

    @abstractmethod
    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        """通用「提示词→文本」接口（路线②③质量闭环的评分/改写/自检复用）。

        Fake 返回确定性伪响应；Real 调用 OpenAI 兼容 chat（失败返回 None，由调用方降级）。
        """
        ...


class FakeLLMClient(LLMClient):
    _counter = 0

    def generate(self, req: GenerationRequest) -> GenerationResult:
        FakeLLMClient._counter += 1
        n = FakeLLMClient._counter

        if req.kind == "doc_question":
            ctx = req.context or {}
            qtype = str(ctx.get("qtype") or "single")
            count = int(ctx.get("count") or 1)
            payloads = _fake_doc_questions(
                req.knowledge_point, qtype, count, ctx.get("existing_stems")
            )
            return GenerationResult(text=f"[fake-doc] {len(payloads)} 题", payloads=payloads)

        if req.kind == "variant":
            module = (req.context or {}).get("module", "职业理念")
            return GenerationResult(
                text=f"[fake-variant] 基于考点《{req.knowledge_point}》的变式题",
                payload=_variant_payload(req.knowledge_point, module, n),
            )
        return GenerationResult(
            text=f"[fake-paragraph] 关于《{req.knowledge_point}》的个性化复盘段落。（AI 生成，仅供参考）"
        )

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        # 确定性伪响应：按提示词标记分发，保证质量闭环在 fake 下走单轮 happy path
        if "【检索相关性评分】" in prompt:
            return '{"relevant": true, "score": 0.9}'
        if "【生成自检】" in prompt:
            return '{"passed": true, "score": 0.9, "issues": []}'
        if "【题目评分】" in prompt:
            return '{"factuality":4,"coverage":4,"uniqueness":4,"explanation":4,"difficulty":4}'
        if "【查询改写】" in prompt:
            marker = "【查询改写】"
            return prompt.split(marker, 1)[-1].strip()
        if "【生成题目】" in prompt:
            import re as _re

            m = _re.search(r"共 (\d+) 道", prompt)
            n = int(m.group(1)) if m else 1
            tm = _re.search(r'type 固定为 "(\w+)"', prompt)
            qtype = tm.group(1) if tm else "single"
            return json.dumps(
                {"questions": _fake_doc_questions("知识点", qtype, n)}, ensure_ascii=False
            )
        return "[fake] ok"


class RealLLMClient(LLMClient):
    """OpenAI 兼容 Chat Completions（真实供应商接线，LLM_MODE=real 启用）。"""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.llm_api_key

    def _chat(self, prompt: str, timeout: int = 30) -> str | None:
        if not self.api_key:
            return None
        body = json.dumps(
            {
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": "你是教资《综合素质》出题与复盘助手，只输出 JSON 或纯文本。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3 if "doc_question" in prompt else 0.7,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                settings.llm_api_base.rstrip("/") + "/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except Exception:
            # D3：不静默吞异常，交由上层决定是否重试/降级
            return None

    def generate(self, req: GenerationRequest) -> GenerationResult:
        if req.kind == "doc_question":
            ctx = req.context or {}
            prompt = build_doc_question_prompt(
                ctx.get("chunks") or [],
                str(ctx.get("qtype") or "single"),
                int(ctx.get("count") or 1),
                str(ctx.get("difficulty") or "medium"),
                ctx.get("focus"),
                ctx.get("existing_stems"),
            )
            # 长上下文生成：超时放宽到 60s
            content = self._chat(prompt, timeout=60)
            if content is None:
                return GenerationResult(text="", payloads=[])
            return GenerationResult(text=content, payloads=parse_doc_questions(content))

        if req.kind == "variant":
            module = (req.context or {}).get("module", "职业理念")
            prompt = (
                f'请出一道教资《综合素质》{module}模块考点《{req.knowledge_point}》的单选变式题，'
                '只输出 JSON：{"module":..., "knowledge_point":..., "stem":..., '
                '"options":[{"key":"A","text":...},...], "answer":["A"], "explanation":..., "type":"single"}'
            )
            content = self._chat(prompt)
            if content is None:
                return GenerationResult(text="", payload=None)
            try:
                payload = json.loads(content[content.index("{") : content.rindex("}") + 1])
                return GenerationResult(text=str(payload.get("stem", "")), payload=payload)
            except Exception:
                return GenerationResult(text="", payload=None)

        content = self._chat(
            f"请为考点《{req.knowledge_point}》写一段 60 字内的个性化复盘鼓励段落，"
            f"结尾必须带「（AI 生成，仅供参考）」。背景数据：{json.dumps(req.context or {}, ensure_ascii=False)}"
        )
        return GenerationResult(text=content or "")

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        return self._chat(prompt, timeout=timeout)


def get_llm_client() -> LLMClient:
    # 接缝 B 的唯一切换点：LLM_MODE=fake（默认/测试，不耗额度）| real。
    if settings.llm_mode == "real" and settings.llm_api_key:
        return RealLLMClient()
    return FakeLLMClient()
