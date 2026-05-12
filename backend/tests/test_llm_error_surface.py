"""LLM 调用失败的可观测性与重试分类。

## 这两件事都是**被实测逼出来的**，不是为了好看

诊断"备用通道 400"时，日志里只有：

    [llm] 全部通道失败: HTTPError: HTTP Error 400: Bad Request

而直接打那家供应商，响应体写得很清楚：

    {"error":{"message":"Access denied, ... overdue-payment","code":"Arrearage"}}

**欠费**。但 `urllib` 的 `HTTPError.__str__` 只有 `HTTP Error 400: Bad Request`，
响应体被丢掉了 —— 于是"账户欠费"看起来像"请求格式写错了"，
**我据此误判成"备用模型配置有误"并写进了文档**。观测性缺口会直接制造错误结论，
这就是它的代价。

同时暴露的第二件事：400 这类**明确拒绝**被当成"瞬时失败"重试
（主模型退避 2s+4s、再切备用重试 2 次），每次调用白花约 6 秒 ——
而重试**不可能**让一个欠费账户变成不欠费。
"""

import io

import pytest

from app.services.llm_client import LLMApiError, RealLLMClient, _describe_http_error


@pytest.mark.parametrize("code,expected", [
    (None, True),   # 连不上/超时之类没有状态码 → 当瞬时失败处理
    (429, True),    # 限流：等一会儿确实可能好
    (500, True),
    (502, True),
    (503, True),
    (400, False),   # 欠费 Arrearage / 参数非法：同一个请求再问一遍得到同一个拒绝
    (401, False),
    (403, False),
    (404, False),
])
def test_哪些失败值得重试(code, expected):
    assert LLMApiError("x", code=code).retryable is expected


class _FakeHTTPError:
    """够 `_describe_http_error` 用的 `HTTPError` 替身（不碰网络）。"""

    def __init__(self, code: int, reason: str, body: bytes | None = None) -> None:
        self.code = code
        self.reason = reason
        self.fp = io.BytesIO(body) if body is not None else None

    def read(self) -> bytes:
        assert self.fp is not None
        return self.fp.read()


def test_响应体被带进错误消息():
    """**这是本文件存在的理由**：4xx 的原因在响应体里，不能丢。"""
    e = _FakeHTTPError(400, "Bad Request", '{"error":{"code":"Arrearage"}}'.encode())
    msg = _describe_http_error(e)
    assert "400" in msg and "Arrearage" in msg, f"响应体没被带出来：{msg}"


def test_响应体为空时也给出可读消息():
    """不能因为"读不出响应体"就抛新错误，也不能留一句空话。"""
    assert "响应体为空" in _describe_http_error(_FakeHTTPError(502, "Bad Gateway"))


def __raiser(calls: list, code: int):
    def _f(*args, **kwargs):
        calls.append(kwargs.get("model") or "primary")
        raise LLMApiError(f"HTTP {code}：模拟", code=code)
    return _f


@pytest.fixture
def _no_sleep(monkeypatch):
    """重试退避是 2s/4s —— 测试里不能真等，否则一个用例 6 秒。"""
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda *_: None)


def test_主模型明确拒绝时不重试(_no_sleep):
    c = RealLLMClient(api_key="k")
    c.fallback = None
    calls: list = []
    c._do_chat = __raiser(calls, 400)
    assert c._chat("p") is None
    assert len(calls) == 1, f"400 是明确拒绝，不该重试，实际调用了 {len(calls)} 次"


def test_限流与5xx仍然重试(_no_sleep):
    c = RealLLMClient(api_key="k")
    c.fallback = None
    calls: list = []
    c._do_chat = __raiser(calls, 429)
    assert c._chat("p") is None
    assert len(calls) == 3, f"429 值得重试，应重试满 3 次，实际 {len(calls)} 次"


def test_备用通道被拒绝后本进程内不再试它(_no_sleep):
    """备用通道欠费时，若不熔断，**每一次调用**都要白等 2 次重试 + 2 秒退避。

    实测：一次基准跑上百个调用，累积成分钟级空转，且日志里刷满同一条失败。
    """
    c = RealLLMClient(api_key="k")
    # 手动设，**不依赖 .env 里恰好配了备用通道**（否则本用例在别的机器上静默变成空测）
    c.fallback = ("https://example.invalid/v1", "k", "备用模型")
    calls: list = []
    c._do_chat = __raiser(calls, 400)

    c._chat("p")                      # 主模型 1 次 + 备用 1 次
    assert calls == ["primary", "备用模型"], calls
    assert c._fallback_off is True, "备用通道明确拒绝后应被标记为不可用"

    c._chat("p")                      # 只打主模型
    assert calls == ["primary", "备用模型", "primary"], "备用通道已被拒绝，不该再试"
