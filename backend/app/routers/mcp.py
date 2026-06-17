"""MCP 传输端点：`POST /mcp`（Streamable HTTP，2026-03-04 版）。

## 三条容易写错的规范细节

1. **通知要回 `202 Accepted`**，不带响应体 —— 它没有 `id`，回一个 JSON-RPC 响应
   反而是协议错误。所以这里先判"是不是通知"，再决定状态码。
2. **`GET` / `DELETE` 一律 `405`**。旧版（2026-03-04 ~ 2026-04-25）用独立的 GET SSE 流，
   本版取消了；`405` 正是规范要求的答复，也是旧版客户端判定"服务端属于新版"的信号之一。
3. **请求体里只能有一条消息**（规范：客户端不得发送 JSON-RPC 响应、不得批量）。
   收到数组或空体都按 `-32600 非法请求` 处理。

## 头校验的宽严取舍

规范把 `MCP-Protocol-Version` 与 `Mcp-Method` 标为**必需**，且版本头必须与请求体
`params._meta` 里的一致。本实现对**缺失宽容、对不一致严格**：

- 缺头 → 放行（按请求体里的版本判定）。宽容缺失不会造成误判；
- 头与请求体**不一致** → `400`。不一致一定是客户端或中间代理出了问题，
  继续执行等于把一次错误的路由当成正常调用，那才是真的危险。

## 权限：**本端点无身份上下文，因此固定只读官方语料**

面试官、桌面客户端都要能直连演示，MCP 里没有（也不该有）考生的登录态。
所以 scope 写死 `NAMESPACE_OFFICIAL` —— 个人资料**不可能**经由此端点泄出。
这不是靠调用方自觉，而是本文件里的一行常量。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session as ORMSession

from ..db import get_db
from ..services.mcp_server import (
    INVALID_REQUEST,
    PROTOCOL_VERSION,
    SUPPORTED_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
    handle_message,
)
from ..services.scope import NAMESPACE_OFFICIAL, Scope
from ..services.tools import ToolContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

HEADER_PROTOCOL_VERSION = "MCP-Protocol-Version"
HEADER_METHOD = "Mcp-Method"
HEADER_NAME = "Mcp-Name"

#: 写作时对照的规范版本（用于响应头与文档），实际支持集见 `SUPPORTED_VERSIONS`
SPEC_REFERENCE = PROTOCOL_VERSION


def _rpc_error(status: int, code: int, message: str, data: dict | None = None) -> JSONResponse:
    err: dict = {"code": code, "message": message}
    if data:
        err["data"] = data
    return JSONResponse(
        status_code=status,
        content={"jsonrpc": "2.0", "id": None, "error": err},
        headers={HEADER_PROTOCOL_VERSION: SPEC_REFERENCE},
    )


def _official_ctx(db: ORMSession) -> ToolContext:
    """MCP 的固定上下文：**只读官方语料**，无候选人。

    `candidate_id=None` 是语义正确的取值（官方语料不属于任何考生），
    也让"忘了传 candidate_id 就泄漏"这类错误根本无从发生。
    """
    return ToolContext(db, Scope(namespace=NAMESPACE_OFFICIAL), candidate_id=None)


@router.post("/mcp")
async def mcp_post(request: Request, db: ORMSession = Depends(get_db)) -> Response:
    try:
        body: Any = await request.json()
    except Exception:
        return _rpc_error(400, -32700, "Parse error：请求体不是合法 JSON")

    if not isinstance(body, dict):
        # 规范禁止批量与响应体；数组到这里一律按非法请求处理
        return _rpc_error(400, INVALID_REQUEST, "Invalid Request：一次只接受一条消息")

    meta_version = (((body.get("params") or {}).get("_meta")) or {}).get(
        "io.modelcontextprotocol/protocolVersion"
    )
    header_version = request.headers.get(HEADER_PROTOCOL_VERSION)
    if header_version and meta_version and header_version != meta_version:
        return _rpc_error(
            400,
            INVALID_REQUEST,
            f"{HEADER_PROTOCOL_VERSION} 头与请求体 _meta 里的版本不一致",
            {"header": header_version, "body": meta_version},
        )
    effective_version = meta_version or header_version
    if effective_version and effective_version not in SUPPORTED_VERSIONS:
        # 版本错误由 handle_message 统一判定；这里只处理"连 _meta 都没有"的情况，
        # 避免同一件事在两处各判一次、口径分叉。
        return _rpc_error(
            400,
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": list(SUPPORTED_VERSIONS), "requested": effective_version},
        )

    header_method = request.headers.get(HEADER_METHOD)
    if header_method and header_method != body.get("method"):
        return _rpc_error(
            400,
            INVALID_REQUEST,
            f"{HEADER_METHOD} 头与请求体的 method 不一致",
            {"header": header_method, "body": body.get("method")},
        )

    payload = handle_message(body, _official_ctx(db))
    if payload is None:
        # 通知：规范要求 202 且无响应体
        return Response(status_code=202)
    return JSONResponse(status_code=200, content=payload)


@router.get("/mcp")
def mcp_get() -> Response:
    """旧版的独立 GET SSE 流在本版已取消 —— `405` 是规范要求的答复。"""
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
def mcp_delete() -> Response:
    """会话删除在本版不存在（协议无状态）—— 同样回 `405`。"""
    return Response(status_code=405, headers={"Allow": "POST"})
