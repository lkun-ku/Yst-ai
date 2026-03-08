"""接缝 B：大模型客户端封装（服务层唯一 AI 出口，Implementation 3）。

测试用 FakeLLMClient，不消耗任何 API 额度（测试决策 33/45）。
真实客户端在票 14 接入（需 API key + 供应商接线）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GenerationRequest:
    kind: str  # "variant" | "review_paragraph"
    knowledge_point: str
    context: dict | None = None


@dataclass
class GenerationResult:
    text: str
    source: str = "realtime"


class LLMClient(ABC):
    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...


class FakeLLMClient(LLMClient):
    def generate(self, req: GenerationRequest) -> GenerationResult:
        if req.kind == "variant":
            return GenerationResult(
                text=f"[fake-variant] 基于考点《{req.knowledge_point}》的变式题", source="realtime"
            )
        return GenerationResult(
            text=f"[fake-paragraph] 关于《{req.knowledge_point}》的个性化复盘段落", source="realtime"
        )


class RealLLMClient(LLMClient):
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def generate(self, req: GenerationRequest) -> GenerationResult:
        raise NotImplementedError("真实 LLM 客户端需 API key + 供应商接线（票 14）")


def get_llm_client() -> LLMClient:
    # 接缝 B 的唯一切换点：测试注入 FakeLLMClient。
    return FakeLLMClient()
