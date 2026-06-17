"""MCP 端点：协议形状、时代探测、以及**权限边界**。

**为什么单列一组用例**：MCP 是唯一一个**没有身份上下文**的出口
（面试官、桌面客户端都要能直连演示）。它有两种失败模式都极其安静：

1. **越权**：某次重构顺手把 scope 从 official 换成 both —— 个人讲义就会
   流向任何调用者，而响应看起来完全正常；
2. **协议不合规**：形状写错（少了 `resultType`、通知回了 200 而不是 202、
   缺 `_meta` 也放行），自己的测试全绿，但**没有任何真实客户端连得上**。

第 1 条靠 `test_MCP端点绝不返回个人资料` 钉住；第 2 条靠逐形状断言钉住。
"""
import json

import pytest

from app.models import Candidate, Document, DocumentChunk
from app.services.mcp_server import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    META_VERSION,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SUPPORTED_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
    handle_message,
    tool_definitions,
)
from app.services.scope import NAMESPACE_OFFICIAL, Scope
from app.services.tools import TOOLS, ToolContext

_TEST_PATH_OFFICIAL = "laws/__test_mcp_official__.md"
_TEST_PATH_PERSONAL = "__test_mcp_personal__.txt"


def _msg(method: str, params: dict | None = None, msg_id=1) -> dict:
    p = dict(params or {})
    p.setdefault("_meta", {META_VERSION: PROTOCOL_VERSION})
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": p}


def _cleanup(db) -> None:
    """删掉本文件播种的文档。

    **测试必须清理自己**：`test_namespace_scope.py` 断言的是官方语料的**精确 id 集合**，
    而 pytest 按字母序执行、`test_mcp_server` 排在它前面 —— 留下的官方文档会让它失败。
    （本仓测试库是会话级重建、不逐用例回滚，所以这种残留能跨文件生效。）
    """
    stale = [r[0] for r in db.query(Document.id).filter(
        Document.storage_path.in_([_TEST_PATH_OFFICIAL, _TEST_PATH_PERSONAL])).all()]
    if not stale:
        return
    db.query(DocumentChunk).filter(DocumentChunk.document_id.in_(stale)).delete(
        synchronize_session=False
    )
    db.query(Document).filter(Document.id.in_(stale)).delete(synchronize_session=False)
    db.commit()


def _seed(db) -> None:
    """一份官方语料 + 一份**别人的**个人资料（用来验证越权）。"""
    _cleanup(db)

    # 本仓测试库是**会话级**重建、不逐用例回滚 —— 已存在就不能再插（会撞主键）
    if db.get(Candidate, 8801) is None:
        db.add(Candidate(id=8801, unionid="mcp8801"))

    off = Document(
        candidate_id=None, is_official=True, title="测试教师法", file_type="md",
        char_count=60, chunk_count=1, status="parsed", storage_path=_TEST_PATH_OFFICIAL,
    )
    per = Document(
        candidate_id=8801, is_official=False, title="某考生的私人讲义", file_type="txt",
        char_count=60, chunk_count=1, status="parsed", storage_path=_TEST_PATH_PERSONAL,
    )
    db.add_all([off, per])
    db.flush()
    db.add_all([
        DocumentChunk(
            document_id=off.id, seq=0,
            content="第七条 教师享有下列权利：进行教育教学活动。",
            heading_path="测试教师法 / 第二章 / 第七条", char_count=24,
        ),
        # 私人讲义里放一个**独有的**字串，一旦泄漏就能被断言抓到
        DocumentChunk(
            document_id=per.id, seq=0,
            content="第七条 我的私人笔记标记串ZZQ：教师享有下列权利。",
            heading_path="某考生的私人讲义 / 第七条", char_count=30,
        ),
    ])
    db.commit()


@pytest.fixture
def ctx(db_session):
    _seed(db_session)
    yield ToolContext(db_session, Scope(namespace=NAMESPACE_OFFICIAL), candidate_id=None)
    _cleanup(db_session)  # teardown：不留残留给后面的用例


# ---------------- 工具清单：不许有第二份 ----------------

def test_工具清单直接来自_tools_不手写第二份():
    """手写第二份会双份维护：tools.py 改了描述，MCP 这边不跟着变，
    于是「Agent 用的工具」与「MCP 客户端看到的工具」悄悄分叉。"""
    defs = tool_definitions()
    assert [d["name"] for d in defs] == [t.name for t in TOOLS]
    for d, spec in zip(defs, TOOLS):
        assert d["inputSchema"] == spec.parameters
        assert d["description"] == spec.description


# ---------------- 协议形状 ----------------

def test_非法请求被判非法():
    assert handle_message("不是对象", None)["error"]["code"] == INVALID_REQUEST
    assert handle_message({"jsonrpc": "1.0", "method": "x"}, None)["error"]["code"] == INVALID_REQUEST
    assert handle_message({"jsonrpc": "2.0"}, None)["error"]["code"] == INVALID_REQUEST


def test_通知不产生响应():
    """规范要求回 202 无体；回一个 JSON-RPC 响应反而是协议错误。"""
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, None) is None


def test_缺少_meta_按参数错误处理(ctx):
    """规范把每请求的 _meta 定为必需（缺失即"请求畸形"）。
    放宽它会让漏字段的客户端拿到看似正常的结果，换客户端时突然失败。"""
    out = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, ctx)
    assert out["error"]["code"] == INVALID_PARAMS
    assert "_meta" in out["error"]["message"]


def test_不支持的协议版本返回规范指定的错误码与支持集(ctx):
    msg = _msg("tools/list", {"_meta": {META_VERSION: "1999-01-01"}})
    out = handle_message(msg, ctx)
    assert out["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert out["error"]["data"]["supported"] == list(SUPPORTED_VERSIONS)
    assert out["error"]["data"]["requested"] == "1999-01-01"


def test_旧版客户端拿到的_initialize_是时代探测信号(ctx):
    """`-32601` 不是"没实现" —— 规范说旧版客户端正是靠**特定错误码**判断
    服务端属于哪个时代（新版无握手），随后改用 server/discover。"""
    out = handle_message(_msg("initialize", {"protocolVersion": "2026-03-04"}), ctx)
    assert out["error"]["code"] == METHOD_NOT_FOUND
    assert "initialize" in out["error"]["message"]


def test_server_discover_的形状(ctx):
    out = handle_message(_msg("server/discover"), ctx)["result"]
    assert out["resultType"] == "complete"
    assert out["supportedVersions"] == [PROTOCOL_VERSION]
    assert "tools" in out["capabilities"]
    # ⚠️ serverInfo 在 **result 的 `_meta`** 里，不在顶层。
    # 这条原先断言的是顶层 —— 那是**错的**，而且**我自己写的测试抓不到我自己的错**：
    # 它只复述了我读规范的理解。真正抓住它的是官方 SDK 来读的那一刻
    # （`client.server_info` 读成 `None`）。这就是互操作测试不可替代的原因。
    assert out["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == SERVER_NAME
    assert "io.modelcontextprotocol/serverInfo" not in out
    assert out["cacheScope"] == "public" and out["ttlMs"] > 0


def test_tools_list_的形状(ctx):
    out = handle_message(_msg("tools/list"), ctx)["result"]
    assert out["resultType"] == "complete"
    assert len(out["tools"]) == 3
    assert all({"name", "description", "inputSchema"} <= set(t) for t in out["tools"])


def test_未知工具回_isError_并列出可用工具(ctx):
    """`isError` 用来给模型**可据以修正的反馈** —— 它换一个工具就能继续。"""
    out = handle_message(_msg("tools/call", {"name": "no_such_tool", "arguments": {}}), ctx)["result"]
    assert out["isError"] is True
    assert "search_kb" in out["content"][0]["text"]


def test_缺少工具名是协议错误而不是业务错误(ctx):
    out = handle_message(_msg("tools/call", {"arguments": {}}), ctx)
    assert out["error"]["code"] == INVALID_PARAMS


def test_参数不合法回_isError(ctx):
    out = handle_message(_msg("tools/call", {"name": "search_kb", "arguments": {}}), ctx)["result"]
    assert out["isError"] is True
    assert "query" in out["content"][0]["text"]


def test_tools_call_正常路径(ctx):
    out = handle_message(
        _msg("tools/call", {"name": "lookup_law", "arguments": {"law": "测试教师法", "article": "第七条"}}),
        ctx,
    )["result"]
    assert "isError" not in out and out["resultType"] == "complete"
    assert out["content"][0]["type"] == "text"
    assert "教师享有下列权利" in out["content"][0]["text"]


def test_ping_回结果里没有业务字段(ctx):
    """`ping` 回一个**不含业务字段**的结果。断言的是「没有业务字段」而不是「等于空字典」——
    因为 `_ok` 会给每个 result 统一盖 `_meta.serverInfo`（规范：SHOULD 在每个 result 上都盖）。"""
    out = handle_message(_msg("ping"), ctx)["result"]
    assert set(out) == {"_meta"}
    assert "io.modelcontextprotocol/serverInfo" in out["_meta"]


# ---------------- 权限边界（最关键的一条）----------------

def test_MCP端点绝不返回个人资料(ctx):
    """**红线**：MCP 没有身份上下文，一旦 scope 被改成 both/both，
    某个考生的私人讲义就会流向任何调用者 —— 而响应看起来完全正常。

    这里用一条**记号字符串**（ZZQ）做断言：只要它出现在响应里，
    就说明越权发生了；反之则证明确实被 scope 挡住了。
    """
    out = handle_message(
        _msg("tools/call", {"name": "search_kb", "arguments": {"query": "教师享有下列权利", "k": 10}}),
        ctx,
    )["result"]
    text = out["content"][0]["text"]
    assert "ZZQ" not in text, "个人资料泄漏到了 MCP 端点"
    assert "私人讲义" not in text


# ---------------- HTTP 传输层 ----------------

def _post(client, body, headers=None):
    return client.post("/mcp", json=body, headers=headers or {})


def test_HTTP_通知返回202(client):
    resp = _post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp.status_code == 202
    assert not resp.content


def test_HTTP_GET与DELETE返回405(client):
    """旧版的独立 GET SSE 流与协议级会话在本版都已取消。"""
    assert client.get("/mcp").status_code == 405
    assert client.delete("/mcp").status_code == 405
    assert client.get("/mcp").headers["allow"] == "POST"


def test_HTTP_非JSON体返回解析错误(client):
    resp = client.post("/mcp", content=b"not-json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32700


def test_HTTP_数组体被拒(client):
    """规范禁止批量：客户端只能发送单条请求或通知。"""
    resp = _post(client, [{"jsonrpc": "2.0", "method": "ping"}])
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == INVALID_REQUEST


def test_HTTP_头与体版本不一致返回400(client):
    """不一致一定是客户端或代理出了问题 —— 继续执行等于把错误路由当正常调用。"""
    resp = _post(
        client,
        _msg("tools/list"),
        headers={"MCP-Protocol-Version": "2026-05-13"},
    )
    assert resp.status_code == 400
    assert "不一致" in resp.json()["error"]["message"]


def test_HTTP_头与体方法不一致返回400(client):
    resp = _post(client, _msg("tools/list"), headers={"Mcp-Method": "tools/call"})
    assert resp.status_code == 400


def test_HTTP_端到端往返(client, ctx):
    """按规范形状走一遍：discover → tools/list → tools/call。"""
    disc = _post(client, _msg("server/discover")).json()["result"]
    assert disc["resultType"] == "complete"
    tools = _post(client, _msg("tools/list")).json()["result"]["tools"]
    name = tools[0]["name"]
    call = _post(
        client,
        _msg("tools/call", {"name": name, "arguments": {"query": "教师享有下列权利"}}),
    ).json()["result"]
    assert call["resultType"] == "complete"
    assert isinstance(call["content"], list) and call["content"][0]["type"] == "text"
