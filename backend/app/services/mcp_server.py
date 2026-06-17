"""把知识库的三个工具按 **MCP（Model Context Protocol）** 暴露出去。

## 为什么做这个

`services/tools.py` 里的 `search_kb` / `lookup_law` / `check_quote` 本来就是给
「问答老师 Agent」用的工具。MCP 让它们**同时**能被任意 MCP 客户端（Claude Desktop、
IDE 助手等）调用 —— 同一份实现、同一套 JSON Schema、同一套参数校验与权限边界，
只是多了一个协议出口。**本模块不新增任何检索能力，只做协议适配。**

## 实现的是哪一版规范，以及为什么是"只读 + 仅官方语料"

对的是 **`2026-03-04`** 这一版（写作时的当前版）。它与旧版有一处**根本差别**：

| | 旧版（2026-03-04 ~ 2026-04-25） | **本次实现的 2026-03-04** |
| --- | --- | --- |
| 握手 | `initialize` + `notifications/initialized` | **无握手**，改为必需的 `server/discover` |
| 状态 | protocol-level session（`Mcp-Session-Id`） | **无状态**：每个请求自带 `_meta` |
| 流 | 独立的 GET SSE 流、可续传 | **取消 GET 流**，GET/DELETE 一律 405 |

无状态这一点对我们是好事：FastAPI 端点天然无状态，不必维护会话表。

**关于旧版客户端**：收到 `initialize` 时我们回 `-32601 Method not found` ——
这不是"没实现"，而是**规范定义的"时代探测"信号**（旧版客户端正是靠错误码判断
服务端属于哪个时代），随后它会改用 `server/discover`。
`GET` / `DELETE` 回 `405` 也是规范对「不支持旧流式传输」的规定动作。

## 权限边界：**只读，且只暴露官方语料**

MCP 端点**没有候选人的身份上下文**（面试官、桌面客户端都要能直连演示），
所以它**绝不能**触达 `personal` 命名空间 —— 那会把某个考生的私人讲义
暴露给任何调用者。因此这里固定用 `Scope(namespace=NAMESPACE_OFFICIAL)`。

注意这不是"靠调用方自觉"：`Scope` 是 `tools.execute` 的必需入参，
而本模块**写死了** official —— 想越权必须改这行代码，而不是传个参数。

## 为什么不引官方 `mcp` SDK

本环境的 pip 源不可达（`mcp` 装不上）。更深一层的原因与本仓一贯取向一致：
为一个单一出口引入协议框架，会把"一处适配"变成"处处跟随它的版本节奏"。
代价必须说清楚 —— 见 ADR-0020 的已知边界：
**没有第三方 MCP 客户端连过本服务**，一致性来自"按规范实现 + 自己的协议级测试"，
不是互操作测试。
"""

from __future__ import annotations

import logging
from typing import Any

from .tools import TOOLS, TOOLS_BY_NAME, ToolContext, execute

logger = logging.getLogger(__name__)

#: 实现的协议版本（无状态那一版）。
PROTOCOL_VERSION = "2026-03-04"
SUPPORTED_VERSIONS: tuple[str, ...] = (PROTOCOL_VERSION,)
SERVER_NAME = "youshitong-kb"
SERVER_VERSION = "1.0.0"

#: 每个请求的 `params._meta` 里携带的协议字段（规范要求，缺失即"请求畸形"）。
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

#: JSON-RPC 2.0 标准错误码 + 规范自定义的版本错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

#: `server/discover` 结果的缓存提示：能力集是静态的，给个较长的 ttl
DISCOVER_TTL_MS = 3600000
#: 工具清单的缓存提示：工具定义随代码发布变化，比 discover 短
TOOLS_TTL_MS = 300000


def tool_definitions() -> list[dict]:
    """MCP 的工具清单 —— **直接由 `tools.TOOLS` 生成，不手写第二份**。

    手写第二份的后果是双份维护：`tools.py` 改了描述或参数，MCP 这边不会跟着变，
    于是"Agent 用的工具"与"MCP 客户端看到的工具"悄悄分叉。
    `ToolSpec.parameters` 本身就是 JSON Schema，正好就是 MCP 要的 `inputSchema`。
    """
    return [
        {
            "name": spec.name,
            "title": spec.description.split("。", 1)[0][:40],
            "description": spec.description,
            "inputSchema": spec.parameters,
        }
        for spec in TOOLS
    ]


def _error(msg_id: Any, code: int, message: str, data: dict | None = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _ok(msg_id: Any, result: dict) -> dict:
    """成功响应。**统一在这里给每个 result 盖 `serverInfo`**。

    规范把 `io.modelcontextprotocol/serverInfo` 定位为 **result 的 `_meta` 键**，
    并说服务端 SHOULD 在**每个** result 上都盖（`mcp_types/_types.py` 的
    `SERVER_INFO_META_KEY` 注释：Reserved result `_meta` key，SDK-managed）。

    ⚠️ 这里原先把它放在 **result 顶层** —— 这个错**是互操作测试抓出来的**：
    官方 SDK 去 `_meta` 里找，于是 `server_info` 读成 `None`。
    我自己那 21 个协议级用例抓不到它，因为它们断言的是**我读规范的理解**；
    换了别人的实现来读，位置错了立刻现形 —— 这就是互操作测试不可替代的地方。
    """
    meta = dict(result.get("_meta") or {})
    meta.setdefault(META_SERVER_INFO, {"name": SERVER_NAME, "version": SERVER_VERSION})
    return {"jsonrpc": "2.0", "id": msg_id, "result": {**result, "_meta": meta}}


def _meta_of(msg: dict) -> dict:
    params = msg.get("params")
    if not isinstance(params, dict):
        return {}
    meta = params.get("_meta")
    return meta if isinstance(meta, dict) else {}


def _check_protocol(msg: dict) -> dict | None:
    """校验请求的协议版本，返回错误响应或 None。

    规范把这个 `_meta` 定为**必需**（"missing these fields renders a request
    malformed"）。所以缺失按 `-32602` 处理，而不是默许 ——
    否则一个漏字段的客户端会拿到看似正常的结果，而那说明我们比规范更宽松，
    宽松的协议实现会在换客户端时突然失败。
    """
    meta = _meta_of(msg)
    version = meta.get(META_VERSION)
    if not version:
        return _error(
            msg.get("id"),
            INVALID_PARAMS,
            f"请求缺少 params._meta[{META_VERSION}]",
        )
    if version not in SUPPORTED_VERSIONS:
        return _error(
            msg.get("id"),
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": list(SUPPORTED_VERSIONS), "requested": version},
        )
    return None


def _discover(msg: dict) -> dict:
    return _ok(
        msg.get("id"),
        {
            "resultType": "complete",
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": {"tools": {}},
            # serverInfo 由 `_ok` 统一放进 `_meta`（不在这里写顶层 —— 那是互操作测试抓出的错）
            "instructions": (
                "教资备考知识库（只读）。可用工具：search_kb（宽召回）、"
                "lookup_law（按法名+条号精确取条文，问题里出现具体条款号时优先用它）、"
                "check_quote（核对一段原文是否真在库中）。"
                "只包含官方考纲与教育法条，不含任何考生私人资料。"
            ),
            "ttlMs": DISCOVER_TTL_MS,
            "cacheScope": "public",
        },
    )


def _list_tools(msg: dict) -> dict:
    return _ok(
        msg.get("id"),
        {
            "resultType": "complete",
            "tools": tool_definitions(),
            "ttlMs": TOOLS_TTL_MS,
            "cacheScope": "public",
        },
    )


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _call_tool(msg: dict, ctx: ToolContext) -> dict:
    """执行一个工具。**业务失败走 `isError`，协议错误走 JSON-RPC error。**

    这条分界是规范的建议（`isError` 用来给模型"可据以修正的反馈"）：
    - `params.name` 缺失 → 请求本身不合法 → `-32602`；
    - 工具名不认识 / 参数不合法 / 工具内部报错 → 都是**这次调用的结果** →
      `isError: true` + 文字说明，让模型能自己改口或换工具。
    """
    params = msg.get("params")
    if not isinstance(params, dict) or not params.get("name"):
        return _error(msg.get("id"), INVALID_PARAMS, "params.name 是必需的")

    name = str(params["name"])
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        available = "、".join(t.name for t in TOOLS)
        return _ok(
            msg.get("id"),
            {
                "resultType": "complete",
                "content": [_text_block(f"未知工具「{name}」。可用工具：{available}")],
                "isError": True,
            },
        )

    raw_args = params.get("arguments")
    args = raw_args if isinstance(raw_args, dict) else {}
    result = execute(spec, args, ctx)
    if not result.get("ok"):
        return _ok(
            msg.get("id"),
            {
                "resultType": "complete",
                "content": [_text_block(f"工具执行失败：{result.get('error') or '未知原因'}")],
                "isError": True,
            },
        )
    return _ok(
        msg.get("id"),
        {
            "resultType": "complete",
            "content": [_text_block(str(result.get("text") or "（没有找到相关内容）"))],
        },
    )


def handle_message(msg: dict, ctx: ToolContext) -> dict | None:
    """处理一条 JSON-RPC 消息 —— **纯函数**（除工具执行外不碰外部状态），因而可单测。

    返回 `None` 表示这是一条**通知**（规范要求回 `202 Accepted`，不带响应体）。
    """
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not msg.get("method"):
        return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "非法请求")

    method = str(msg["method"])
    # 通知没有 id，且不应收到响应
    if method.startswith("notifications/"):
        return None

    bad_version = _check_protocol(msg)
    if bad_version is not None:
        return bad_version

    if method == "server/discover":
        # 可观测性：MCP 端点没有身份上下文，客户端自报的名字是唯一能回答
        # "谁在用这个端点"的信息（也是排查"某个客户端行为异常"时的起点）。
        info = _meta_of(msg).get(META_CLIENT_INFO) or {}
        logger.info(
            "MCP server/discover：client=%s/%s caps=%s",
            info.get("name", "?"),
            info.get("version", "?"),
            sorted((_meta_of(msg).get(META_CLIENT_CAPS) or {}).keys()),
        )
        return _discover(msg)
    if method == "tools/list":
        return _list_tools(msg)
    if method == "tools/call":
        return _call_tool(msg, ctx)
    if method == "ping":
        # 规范未在本版列出该方法；回一个空结果是无害且被广泛预期的行为。
        return _ok(msg.get("id"), {})
    # 旧版客户端会先发 `initialize` 探测时代 —— `-32601` 正是它期待的答复
    # （规范：客户端靠"特定错误码"判断服务端属于哪个时代）。
    return _error(msg.get("id"), METHOD_NOT_FOUND, f"Method not found: {method}")
