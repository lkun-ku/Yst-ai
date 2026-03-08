"""内容安全客户端占位（Implementation 29）。

真实实现接微信 msgSecCheck（票 13）。MVP 期占位：输入/输出两侧调用点已留，默认放行。
"""

from abc import ABC, abstractmethod


class ContentSafetyClient(ABC):
    @abstractmethod
    def check_text(self, text: str) -> bool:
        """返回 True 表示内容安全可通过。"""

    def check_input(self, text: str) -> bool:
        return self.check_text(text)

    def check_output(self, text: str) -> bool:
        return self.check_text(text)


class StubContentSafetyClient(ContentSafetyClient):
    def check_text(self, text: str) -> bool:
        return True


def get_content_safety() -> ContentSafetyClient:
    return StubContentSafetyClient()
