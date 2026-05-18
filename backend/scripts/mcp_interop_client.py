"""用**官方 mcp SDK** 连一次本服务 —— MCP 的**互操作**测试（不是自测）。

## 为什么必须由外部客户端来做

`tests/test_mcp_server.py` 的 21 个用例测的是"我按规范实现的那几个函数"，
请求形状**由我自己写**。它们能证明实现符合我读到的规范，**证明不了别人的客户端连得上** ——
而那正是 ADR-0020 里登记的最大弱点：**没有任何第三方 MCP 客户端连过本服务**。
本脚本补的就是这一条：请求由官方 SDK 发出，形状不受我控制。

## 三条判定标准（缺一条不算通过）

1. **握手**：官方客户端能完成版本协商 —— 不是靠服务端"宽容缺失"，而是它主动声明版本；
2. **工具清单**：`tools/list` 的三个工具，名字与 `inputSchema` 与 `tools.TOOLS` 一致
   （服务端的清单是**由 `tools.TOOLS` 生成**的，所以这里同时验证了"没有手写第二份"）；
3. **调用**：`tools/call` 真能取回依据，且**未知工具返回的是错误而不是空结果**
   （把错误当成"查不到"是最容易骗过自己的失败形态）。

## 跑法（SDK 不进项目依赖，装在一个丢弃式 venv 里）

    python -m venv .scratch/mcp-interop
    .scratch/mcp-interop/Scripts/python.exe -m pip install mcp
    cd backend && python -m uvicorn app.main:app --port 8123
    .scratch/mcp-interop/Scripts/python.exe backend/scripts/mcp_interop_client.py

`--url` 可改目标；默认 `http://127.0.0.1:8123/mcp`。
"""

from __future__ import annotations

import argparse
import asyncio
import json

from mcp.client.client import Client
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _text_of(result) -> str:
    """从 `call_tool` 的返回里取出文本块（形状由官方 SDK 决定，不按我的猜测解析）。"""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


async def run(url: str) -> int:
    failures: list[str] = []
    print(f"连接 {url}", flush=True)
    # 传 **URL 字符串**即可：`Client.__post_init__` 对它走
    # `_connect_transport(streamable_http_client(url))`（`client.py` 第 393 行）。
    # 试过传 `StreamableHTTPTransport(url)` —— 它**不支持异步上下文协议**，会直接 TypeError。
    async with Client(url) as client:
        # 1) 握手：协商出来的版本与客户端自报的身份
        print(f"  protocol_version = {client.protocol_version}")
        info = client.server_info
        print(f"  server_info      = {getattr(info, 'name', None)} / {getattr(info, 'version', None)}")
        if not client.protocol_version:
            failures.append("没有协商出版本")
        if info is None:
            # 第一版就是在这里读成 None 的：我把 serverInfo 放在了 result 顶层，
            # 而规范/SDK 都要求它在 result 的 `_meta` 里。**这条断言就是为它加的**。
            failures.append(
                "server_info 读不到 —— serverInfo 必须在 result 的 `_meta` 里（不是顶层）"
            )

        # 2) 工具清单
        listed = await client.list_tools()
        tools = {t.name: t for t in listed.tools}
        print(f"  tools/list       = {sorted(tools)}")
        for expect in ("search_kb", "lookup_law", "check_quote"):
            if expect not in tools:
                failures.append(f"工具清单缺少 {expect}")
        if "search_kb" in tools:
            # ⚠️ 官方 SDK 的模型字段名是 **snake_case**（`input_schema`）而不是 MCP 线上的
            # camelCase（`inputSchema`）—— pydantic 模型内部用 Python 命名。第一版我按线上
            # 名字读，直接 `AttributeError` 把后半段（call_tool）也一起中断了。
            schema = getattr(tools["search_kb"], "input_schema", None) or getattr(
                tools["search_kb"], "inputSchema", None
            )
            props = list((schema or {}).get("properties") or {})
            print(f"  search_kb 的入参  = {props}")
            if not props:
                failures.append("search_kb 的 inputSchema 是空的（清单没从 tools.TOOLS 带出来）")

        # 3) 真调用：取一条法条
        try:
            got = await client.call_tool("lookup_law", {"law": "教师法", "article": "第七条"})
            text = _text_of(got)
            print(f"  lookup_law 返回    {len(text)} 字，含「第七条」= {'第七条' in text}")
            if "第七条" not in text:
                failures.append("lookup_law 没取回第七条（调用通道可能没真正打通）")
        except Exception as e:  # noqa: BLE001 — 互操作测试要把失败**如实报出来**，不吞
            failures.append(f"call_tool 抛异常：{type(e).__name__}: {e}")

        # 4) 未知工具必须被**标记为错误**，不能当成一次正常的空结果（那会把故障伪装成「查不到」）
        #
        # ⚠️ 第一版我断言的是「SDK 会抛异常」—— **这是错的**：规范里 `isError=true` 就是"错误"，
        # 而它是一个**正常返回**，SDK 不会抛。按"抛异常"断言会把一个**符合规范**的服务判成失败 ——
        # **断言写错比不写更危险**。
        try:
            bad = await client.call_tool("no_such_tool", {})
            flag = getattr(bad, "is_error", None)
            if flag is None:
                flag = getattr(bad, "isError", None)
            text = _text_of(bad)
            print(f"  未知工具          isError={flag} · {text[:60]}")
            if not flag:
                failures.append("未知工具既没抛错也没有 isError —— 故障会被伪装成「查不到」")
        except Exception as e:  # noqa: BLE001 — 以异常形式报错也算"报错"，可接受
            print(f"  未知工具          以异常形式报错：{type(e).__name__}")

    print()
    if failures:
        print("❌ 互操作测试失败：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ 互操作测试通过：官方 SDK 能握手、列工具、调工具，且未知工具报错")
    return 0


async def run_low_level(url: str) -> int:
    """**低层路径**：`streamable_http_client` + `ClientSession`，绕开高层 `Client`。

    为什么要单独走一遍：高层 `Client(url)` 在本环境报 `MCPError: Not Found`，
    且**一个 HTTP 请求都没发出**（服务端访问日志为证）。换低层是为了把两件事分开判定 ——
    到底是**本服务的协议实现**有问题，还是 **SDK 高层 Client 的那条 URL 分支**有问题。
    两者混在一个失败里就没法往下查。

    这里手动走**现代握手** `server/discover`，不走 `initialize` —— 后者在本服务上
    按规范返回 `-32601`（那是旧版客户端的**时代探测信号**，不是缺陷），
    走它只会得到一个"预期内的失败"，证明不了任何东西。
    """
    print(f"低层路径连接 {url}", flush=True)
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            raw = await session.send_discover("2026-03-30")
            print(f"  server/discover → {type(raw).__name__}", flush=True)
            listed = await session.list_tools()
            names = sorted(t.name for t in listed.tools)
            print(f"  tools/list      → {names}")
            if not names:
                return 1
            got = await session.call_tool("lookup_law", {"law": "教师法", "article": "第七条"})
            text = _text_of(got)
            print(f"  lookup_law      → {len(text)} 字，含「第七条」= {'第七条' in text}")
            if "第七条" not in text:
                return 1
    print("✅ 低层路径通过：官方客户端能对本服务完成现代握手、列工具、调工具")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8123/mcp")
    ap.add_argument(
        "--low-level",
        action="store_true",
        help="走 streamable_http_client + ClientSession（绕开高层 Client，用于分离故障面）",
    )
    args = ap.parse_args()
    try:
        return asyncio.run(run_low_level(args.url) if args.low_level else run(args.url))
    except Exception as e:  # noqa: BLE001 — 互操作失败必须**如实报出来**，不吞
        # 把**整条异常链**打出来：`MCPError: Not Found` 单看会误导 ——
        # 它可能是 HTTP 层的 4xx 被传输层映射过来的（没有 JSON-RPC 体时就是这么映射的），
        # 而不是服务端真的回了某个 JSON-RPC 错误码。看链才分得清。
        print(f"❌ 连接/握手失败：{type(e).__name__}: {e}")
        cur = e
        while cur.__cause__ or cur.__context__:
            cur = cur.__cause__ or cur.__context__
            print(f"   caused by {type(cur).__name__}: {str(cur)[:220]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
