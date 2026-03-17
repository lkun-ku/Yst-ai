"""接缝 B：大模型客户端封装（服务层唯一 AI 出口，Implementation 3）。

- FakeLLMClient：测试用假实现，不消耗任何 API 额度（测试决策 33/45）。
- RealLLMClient：OpenAI 兼容 Chat Completions 真实实现（LLM_MODE=real 启用）。
- variant 产物要求结构化 payload（进入管线前仍过结构化校验，不通过即弃）。
"""

import json
import re
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import settings
from .prompts_kb import kb_question_prompt


@dataclass
class GenerationRequest:
    kind: str  # "variant" | "review_paragraph"
    knowledge_point: str
    context: dict | None = None


@dataclass
class GenerationResult:
    text: str
    source: str = "realtime"
    payload: dict | None = field(default=None)  # variant 的结构化题目产物（单题）
    payloads: list[dict] | None = field(default=None)  # doc_question 的结构化题目列表


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


class LLMClient(ABC):
    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...


class FakeLLMClient(LLMClient):
    _counter = 0

    def generate(self, req: GenerationRequest) -> GenerationResult:
        FakeLLMClient._counter += 1
        n = FakeLLMClient._counter
        if req.kind == "variant":
            module = (req.context or {}).get("module", "职业理念")
            return GenerationResult(
                text=f"[fake-variant] 基于考点《{req.knowledge_point}》的变式题",
                payload=_variant_payload(req.knowledge_point, module, n),
            )
        if req.kind == "doc_question":
            ctx = req.context or {}
            qtype = ctx.get("qtype", "single")
            count = int(ctx.get("count", 1) or 1)
            payloads = _fake_doc_questions("文档-考点", qtype, count)
            return GenerationResult(
                text=payloads[0].get("stem", "") if payloads else "",
                payloads=payloads,
            )
        return GenerationResult(
            text=f"[fake-paragraph] 关于《{req.knowledge_point}》的个性化复盘段落。（AI 生成，仅供参考）"
        )

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        """离线确定性回应，按提示词标记分发（出题 / 相关性 / 自检 / 改写）。"""
        if "【生成题目】" in prompt:
            tm = re.search(r'type 固定为 "(\w+)"', prompt)
            qtype = tm.group(1) if tm else "single"
            cm = re.search(r"生成 (\d+) 道", prompt)
            count = int(cm.group(1)) if cm else 1
            items = _fake_doc_questions("个人资料-考点", qtype, count)
            return json.dumps({"questions": items}, ensure_ascii=False)
        if "【检索相关性评分】" in prompt:
            return '{"relevant": true, "score": 0.9}'
        if "【生成自检】" in prompt:
            return '{"passed": true, "score": 0.9, "issues": []}'
        if "【查询改写】" in prompt:
            # 原查询附在标记之后，直接回退，避免改写死循环
            return prompt.split("【查询改写】", 1)[1]
        return ""  # 兜底：空输出，上层按「无产出」降级


class RealLLMClient(LLMClient):
    """OpenAI 兼容 Chat Completions（真实供应商接线，LLM_MODE=real 启用）。"""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.llm_api_key

    def _chat(self, prompt: str) -> str | None:
        if not self.api_key:
            return None
        body = json.dumps(
            {
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": "你是教资《综合素质》出题与复盘助手，只输出 JSON 或纯文本。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
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
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    def generate(self, req: GenerationRequest) -> GenerationResult:
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

        if req.kind == "doc_question":
            ctx = req.context or {}
            prompt = kb_question_prompt(
                ctx.get("chunks", []),
                ctx.get("qtype", "single"),
                int(ctx.get("count", 1) or 1),
                ctx.get("difficulty", "medium"),
                ctx.get("focus"),
                ctx.get("existing_stems"),
                ctx.get("scope"),
            )
            content = self._chat(prompt)
            if content is None:
                return GenerationResult(text="", payloads=[])
            try:
                payloads = parse_doc_questions(content)
            except Exception:
                payloads = []
            return GenerationResult(
                text=payloads[0].get("stem", "") if payloads else "",
                payloads=payloads,
            )

        content = self._chat(
            f"请为考点《{req.knowledge_point}》写一段 60 字内的个性化复盘鼓励段落，"
            f"结尾必须带「（AI 生成，仅供参考）」。背景数据：{json.dumps(req.context or {}, ensure_ascii=False)}"
        )
        return GenerationResult(text=content or "")

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        return self._chat(prompt)


def get_llm_client() -> LLMClient:
    # 接缝 B 的唯一切换点：LLM_MODE=fake（默认/测试，不耗额度）| real。
    if settings.llm_mode == "real" and settings.llm_api_key:
        return RealLLMClient()
    return FakeLLMClient()


_FAKE_Q_IDX = 0  # 全局自增，保证跨多次调用生成的题目全局唯一（避免被 _persist_questions 去重丢弃）


def _fake_doc_questions(module: str, qtype: str, count: int) -> list[dict]:
    """构造可通过结构化校验的伪题目（kb_generate/kb_graph 测试与 FakeLLMClient 复用）。

    module 固定为「个人资料」，跳过官方模块的考点归属校验；按题型补齐 options/answer。
    题干 / 考点用全局自增下标，确保多次调用之间不重复（否则补偿轮被去重）。
    """
    global _FAKE_Q_IDX
    items: list[dict] = []
    for _ in range(count):
        i = _FAKE_Q_IDX
        _FAKE_Q_IDX += 1
        base = {
            "module": "个人资料",
            "knowledge_point": f"{module}-{i}",
            "stem": (
                f"（伪题{i}）下列关于《{module}》的表述，正确的是？"
                if qtype != "blank"
                else f"（伪填空{i}）___ 是培养人的社会活动。"
            ),
            "explanation": f"本题考查《{module}》核心要点。",
            "type": qtype,
        }
        if qtype in ("single", "multiple", "judge"):
            if qtype == "judge":
                base["options"] = [
                    {"key": "A", "text": f"{module} 正确表述{i}"},
                    {"key": "B", "text": f"{module} 错误表述{i}"},
                ]
                base["answer"] = ["A"]
            else:
                base["options"] = [
                    {"key": "A", "text": f"{module} 正确表述{i}"},
                    {"key": "B", "text": f"{module} 常见误解{i}"},
                    {"key": "C", "text": f"{module} 无关表述{i}"},
                    {"key": "D", "text": f"{module} 颠倒表述{i}"},
                ]
                base["answer"] = ["A"] if qtype == "single" else ["A", "B"]
        elif qtype == "blank":
            base["answer"] = [f"答案{i}"]
        else:  # short
            base["answer"] = f"参考答案文本{i}"
        items.append(base)
    return items


def parse_doc_questions(text: str) -> list[dict]:
    """解析知识库出题 LLM 输出为题目 dict 列表（与 prompts_kb._extract_json 同源）。

    期望输出形如 {"questions":[{...}]}（见 kb_question_prompt）；也兼容直接返回 JSON 数组。
    解析失败或非列表结构返回空列表（上层视为该批欠产，走补偿轮次或降级，不抛异常）。
    """
    if not text:
        return []
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    try:
        s = t.index("{")
        e = t.rindex("}") + 1
        obj = json.loads(t[s:e])
    except Exception:
        try:
            a = t.index("[")
            b = t.rindex("]") + 1
            obj = json.loads(t[a:b])
        except Exception:
            return []
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        items = obj.get("questions") or []
    else:
        return []
    return [it for it in items if isinstance(it, dict)]
