"""接缝 B：大模型客户端封装（服务层唯一 AI 出口，Implementation 3）。

- FakeLLMClient：测试用假实现，不消耗任何 API 额度（测试决策 33/45）。
- RealLLMClient：OpenAI 兼容 Chat Completions 真实实现（LLM_MODE=real 启用）。
- variant 产物要求结构化 payload（进入管线前仍过结构化校验，不通过即弃）。
"""

import json
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import settings


@dataclass
class GenerationRequest:
    kind: str  # "variant" | "review_paragraph"
    knowledge_point: str
    context: dict | None = None


@dataclass
class GenerationResult:
    text: str
    source: str = "realtime"
    payload: dict | None = field(default=None)  # variant 的结构化题目产物


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
        return GenerationResult(
            text=f"[fake-paragraph] 关于《{req.knowledge_point}》的个性化复盘段落。（AI 生成，仅供参考）"
        )


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

        content = self._chat(
            f"请为考点《{req.knowledge_point}》写一段 60 字内的个性化复盘鼓励段落，"
            f"结尾必须带「（AI 生成，仅供参考）」。背景数据：{json.dumps(req.context or {}, ensure_ascii=False)}"
        )
        return GenerationResult(text=content or "")


def get_llm_client() -> LLMClient:
    # 接缝 B 的唯一切换点：LLM_MODE=fake（默认/测试，不耗额度）| real。
    if settings.llm_mode == "real" and settings.llm_api_key:
        return RealLLMClient()
    return FakeLLMClient()
