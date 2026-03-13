"""内容安全客户端（票 13 / Implementation 29）。

- StubContentSafetyClient：MVP 占位，默认放行（测试可 monkeypatch）。
- WxContentSafetyClient：真实接入微信 msgSecCheck（需 WX_APPID/WX_SECRET 配置），
  未配置 access_token 或调用失败时放行失败（返回 False，宁可拦截不可漏放）由调用方降级。
通过 CONTENT_SAFETY_MODE 环境变量切换：stub（默认）| wx。
"""

import json
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

from ..config import settings


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


class WxContentSafetyClient(ContentSafetyClient):
    """微信 msgSecCheck 真实接入（票 13）。

    access_token 以 appid/secret 换取并缓存至过期；检测调用失败视为不通过（合规优先）。
    """

    _token: str | None = None
    _token_expire_at: float = 0.0

    def _get_access_token(self) -> str | None:
        if self._token and time.time() < self._token_expire_at:
            return self._token
        url = (
            "https://api.weixin.qq.com/cgi-bin/token?"
            + urllib.parse.urlencode(
                {"grant_type": "client_credential", "appid": settings.wx_appid, "secret": settings.wx_secret}
            )
        )
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        token = data.get("access_token")
        if not token:
            return None
        WxContentSafetyClient._token = token
        WxContentSafetyClient._token_expire_at = time.time() + int(data.get("expires_in", 7200)) - 300
        return token

    def check_text(self, text: str) -> bool:
        token = self._get_access_token()
        if not token:
            return False  # 无法检测时视为不通过（合规优先）
        url = f"https://api.weixin.qq.com/wxa/msg_sec_check?access_token={token}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps({"content": text[:2500]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return False
        return data.get("errcode") == 0


def get_content_safety() -> ContentSafetyClient:
    if settings.content_safety_mode == "wx" and settings.wx_appid and settings.wx_secret:
        return WxContentSafetyClient()
    return StubContentSafetyClient()
