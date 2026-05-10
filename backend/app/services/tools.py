"""工具层：注册表 + 参数校验 + 执行 + 决策（问答老师 Agent 的"手"）。

## 为什么只有三个工具

工具的粒度决定 Agent 的可控性。三个工具各自对应一种**不可互相替代**的能力：

| 工具 | 解决什么 | 为什么不能合并进检索 |
| --- | --- | --- |
| `search_kb` | 宽召回：不知道在哪，先找 | 它是唯一"开放式"的入口 |
| `lookup_law` | **精确取条文**：已知法名 + 条号 | **结构化定位，不走向量** —— 见下 |
| `check_quote` | 校验一段引用是否真在库里 | 它是"自检"动作，检索做不到 |

**`lookup_law` 是本项最重要的一条设计**：第 5、6 两项反复验证了同一个结构事实 ——
法名与条号只出现在 `heading_path` 里（正文没有），因此**"《教师法》第七条"这类问题
用向量检索与 BM25 都做不到可靠**。而它其实是个**结构化查询**：
`heading_path` 里同时含法名与条号即可精确定位，零模型、零向量、结果确定。
把这个判断做成工具交给模型，比让它反复改写检索词去碰运气可靠得多。

## 决策为什么用「结构化文本契约」而不是原生 function calling

OpenAI 兼容接口的原生 `tools=[...]` 更省一次解析，但有两个现实约束：
① 本仓的 `LLMClient` 是**纯文本接缝**（`ask(prompt) -> str`），不是 `BaseChatModel`；
② 原生工具调用**无法离线测试**（`LLM_MODE=fake` 下没有真实供应商），
   于是它必然成为又一条"只能标注未验证"的分支。

因此这里用「让模型输出一段 JSON 决策」的契约：**任何文本模型都能用**，
且 Fake 实现能给出确定性决策，链路可以端到端离线测试。
代价是多一次解析（且需要模型遵守 JSON 格式）。
**反转条件**：若供应商支持原生 function calling 且离线可测（例如本地小模型），
应改为原生以省掉这次解析 —— 但那需要先解决 ②，否则只是把不可验证的面又扩大一块。

## 失败是一等状态，不是异常

`execute()` **永不抛异常**：参数不合法、工具内部报错、结果为空，都返回
`{"ok": False, "error": ...}`。理由：把"某一步查不到"升级成"整个问答失败"是错的 ——
正确答案往往是"用已有材料作答，并说明缺口在哪"。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from ..config import settings

logger = logging.getLogger(__name__)

#: 参数长度上限：挡住模型把整段资料当 query 塞进来（既费 token 又会拖垮检索）。
MAX_ARG_CHARS = 200
#: 单个工具返回的字符上限，防止一次观察把上下文打满。
MAX_RESULT_CHARS = 4000


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整声明。`parameters` 是 **JSON Schema 的子集**（见 `validate_args`）。"""

    name: str
    description: str
    parameters: dict
    handler: Callable[..., dict]

    def as_prompt_block(self) -> str:
        """给模型看的工具说明。"""
        props = self.parameters.get("properties") or {}
        required = set(self.parameters.get("required") or [])
        lines = [f"- {self.name}：{self.description}"]
        for pname, pspec in props.items():
            mark = "（必填）" if pname in required else "（可选）"
            desc = pspec.get("description") or pspec.get("type") or ""
            lines.append(f"    参数 {pname}{mark}：{desc}")
        return "\n".join(lines)


class ToolContext:
    """工具执行所需的运行时上下文（检索范围、库连接、embed 函数）。

    显式传对象而不是散装参数：加一个依赖只改这里，不改所有 handler 的签名。
    """

    def __init__(self, db, scope, candidate_id: int, embed_fn=None) -> None:
        self.db = db
        self.scope = scope  # Scope 对象（命名空间 + 科目 + 文档过滤）
        self.candidate_id = candidate_id
        self.embed_fn = embed_fn


# ---------------- 参数校验（JSON Schema 子集，零新依赖） ----------------

def validate_args(spec: ToolSpec, args: Any) -> list[str]:
    """校验工具入参，返回错误列表（空 = 通过）。

    只实现用得到的子集：`type` / `required` / `enum` / 字符串长度。
    引入 `jsonschema` 只为这几个键不值当 —— 而**不校验**则会让模型
    传进来的畸形参数一路走到 SQL 层。
    """
    errors: list[str] = []
    if not isinstance(args, dict):
        return ["args 必须是对象"]

    schema = spec.parameters or {}
    for key in schema.get("required") or []:
        if args.get(key) in (None, ""):
            errors.append(f"缺少必填参数 {key}")

    for key, value in args.items():
        pspec = (schema.get("properties") or {}).get(key)
        if pspec is None:
            continue  # 多余参数忽略而不是报错：模型偶尔多带一个键，不该算失败
        expected = pspec.get("type")
        if expected == "string":
            if not isinstance(value, str):
                errors.append(f"参数 {key} 应为字符串")
            elif len(value) > MAX_ARG_CHARS:
                errors.append(f"参数 {key} 过长（上限 {MAX_ARG_CHARS} 字）")
        elif expected == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"参数 {key} 应为整数")
            elif "minimum" in pspec and value < pspec["minimum"]:
                errors.append(f"参数 {key} 不能小于 {pspec['minimum']}")
            elif "maximum" in pspec and value > pspec["maximum"]:
                errors.append(f"参数 {key} 不能大于 {pspec['maximum']}")
        if "enum" in pspec and value not in pspec["enum"]:
            errors.append(f"参数 {key} 只能是 {pspec['enum']} 之一")
    return errors


# ---------------- 执行 ----------------

def execute(spec: ToolSpec, args: dict, ctx: ToolContext) -> dict:
    """执行工具。**永不抛异常** —— 失败返回 `{"ok": False, "error": ...}`。

    返回结构固定为 `{"ok": bool, "error": str, "items": [...], "text": str}`，
    这样调用方处理"成功/失败"是同一个分支，不需要 try/except 包住每一步。
    """
    errors = validate_args(spec, args)
    if errors:
        return {"ok": False, "error": "；".join(errors), "items": [], "text": ""}
    # **按 schema 过滤**后再传给 handler：`validate_args` 说"多余参数忽略"，
    # 但不在这里真的丢掉它们，`**args` 会把未知键透传进去 → TypeError → 一次调用白扔。
    # （这个缺口是被 `test_多余参数忽略而不是报错` 抓出来的。）
    declared = set((spec.parameters or {}).get("properties") or {})
    kwargs = {k: v for k, v in args.items() if k in declared}
    try:
        out = spec.handler(ctx=ctx, **kwargs) or {}
    except Exception as exc:  # 工具失败是一等状态，不是异常
        logger.warning("工具 %s 执行失败：%s: %s", spec.name, type(exc).__name__, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "items": [], "text": ""}

    items = out.get("items") or []
    text = str(out.get("text") or "")
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS]
    if not items and not str(out.get("note") or ""):
        # 空结果是**正常**结果（库里确实没有），但要显式表达，
        # 免得模型把"没查到"与"工具坏了"混为一谈。
        return {"ok": True, "error": "", "items": [], "text": text or "（没有找到相关内容）"}
    return {"ok": True, "error": "", "items": items, "text": text, "note": out.get("note", "")}


# ---------------- 三个工具 ----------------

def _search_kb(query: str, k: int = 6, ctx: ToolContext = None) -> dict:
    """宽召回：向量 ⊕ 关键词 → RRF（含精排，见 kb_retrieval.retrieve）。"""
    from .kb_retrieval import retrieve

    chunks = retrieve(ctx.db, query, ctx.scope, k=k, embed_fn=ctx.embed_fn)
    return {
        "items": chunks,
        "text": "\n".join(
            f"[依据 #{c.get('id')}｜{c.get('heading_path') or '未分章'}]\n{c.get('content') or ''}"
            for c in chunks
        ),
    }


#: 问题里的「X第N条」——含可选的 `《》`、允许中间有空白。
#: 法名要求以「法」或「条例」结尾：这挡掉了大量误识别（如「教师法律」不会匹配），
#: 而库里 6 部语料全部以这两个字收尾。
_LAW_REF_RE = re.compile(
    r"《?([\u4e00-\u9fa5]{2,20}?(?:法|条例))》?\s*第\s*([〇零一二三四五六七八九十百0-9]{1,8})\s*条"
)

#: 正式全称的前缀。库里语料的法名是简称（`义务教育法`），而用户很可能写全称 ——
#: 不剥掉前缀会让 `LIKE %中华人民共和国义务教育法%` 匹配不到 `义务教育法 / 第十五条`。
_OFFICIAL_PREFIXES = ("中华人民共和国",)


def find_law_reference(question: str) -> dict | None:
    """从问题里识别「法名 + 条号」，返回 `lookup_law` 的参数；识别不到返回 `None`。

    ## 为什么这件事要用正则做**确定性**识别，而不是交给模型判断

    「《X》第N条」是**结构化查询**，不是相似度问题 —— 第 5、6 两项反复验证了同一个结构事实：
    **法名与条号只出现在 `heading_path` 里，正文没有**，所以向量与 BM25 都做不到可靠
    （`lookup_law` 的模块 docstring 里记了这条）。

    交给模型自己决定"要不要查条文"，等于把一次确定性定位变成一次碰运气。实测后果很具体：
    `grounded`（只有 `search_kb`）在**条号类问题**上误拒 —— 真实模型下误拒率 **0.1**，
    且那 2 条误拒样本**都是**「《X》第N条 是怎么规定的」；同一类问题在 `agent`
    （可调 `lookup_law`）上误拒率 **0.0**。所以这里补的是 grounded 缺的那一步，
    **而不是**把 grounded 变成 agent（那会毁掉"无工具 vs 有工具"这个对照维度）。

    识别到但库里没有该条号时无副作用：`lookup_law` 精确复核全灭会**如实返回空**，
    既不产生证据也不改变拒答判定 —— 即本函数是**纯增强，不会引入新的误答**。
    """
    m = _LAW_REF_RE.search(question or "")
    if not m:
        return None
    law = m.group(1)
    for prefix in _OFFICIAL_PREFIXES:
        if law.startswith(prefix) and len(law) > len(prefix) + 1:
            law = law[len(prefix):]
            break
    return {"law": law, "article": f"第{m.group(2)}条"}


def _lookup_law(law: str = "", article: str = "", ctx: ToolContext = None) -> dict:
    """**结构化**取条文：在 `heading_path` 里同时匹配法名与条号。

    不走向量、不算 BM25 —— 这是"《教师法》第七条"这类问题的**正解**：
    它本质是一次结构化查询，而不是相似度问题。
    （第 5、6 两项已验证：法名与条号只在 heading 里，向量与 BM25 都做不可靠。）

    实现上用 `LIKE` 同时匹配两段，并**在应用层复核**：`LIKE` 会漏掉
    「第七十七条」被 `%第七条%` 命中的误报等等价情况，复核能把它们挡掉。
    """
    from sqlalchemy import and_, or_, select

    from ..models import Document, DocumentChunk
    from .kb_retrieval import _chunk_row, _clean
    from .scope import NAMESPACE_BOTH, NAMESPACE_OFFICIAL, NAMESPACE_PERSONAL

    if not law and not article:
        return {"items": [], "text": "", "note": "法名与条号至少给一个"}

    # 命名空间条件与检索层同源：工具也不能越过权限边界
    if ctx.scope.namespace == NAMESPACE_OFFICIAL:
        ns = Document.is_official.is_(True)
    elif ctx.scope.namespace == NAMESPACE_BOTH:
        ns = or_(Document.candidate_id == ctx.candidate_id, Document.is_official.is_(True))
    else:
        ns = and_(Document.candidate_id == ctx.candidate_id, Document.is_official.is_(False))

    conds = [ns, DocumentChunk.heading_path.isnot(None)]
    if law:
        conds.append(DocumentChunk.heading_path.like(f"%{law}%"))
    if article:
        conds.append(DocumentChunk.heading_path.like(f"%{article}%"))

    rows = (
        ctx.db.execute(
            select(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(*conds)
            .limit(20)
        )
        .scalars()
        .all()
    )

    # 应用层复核：`LIKE %第七条%` 会命中「第七十七条」（子串），必须挡掉。
    # 判据：heading_path 的**最后一段**（条号段）要精确等于 article。
    def _is_exact(row) -> bool:
        if not article:
            return True
        tail = (row.heading_path or "").rsplit(" / ", 1)[-1].strip()
        return tail == article.strip()

    kept = [r for r in rows if _is_exact(r)]
    if not kept and article:
        # 精确复核全灭 → 说明给出的条号在库里不存在。**如实返回空**，
        # 而不是把 LIKE 的模糊命中当答案 —— 那正是"编造条文"的一种形态。
        return {
            "items": [],
            "text": "",
            "note": f"未找到 {law}{article}（{rows and '有相近条号但不等' or '无匹配'}）",
        }
    # 用 `_clean` 剥掉内部键（embedding / has_vec）：两条检索路径对外字段一致，
    # 工具的返回也不该把向量漏出去（体积 + 语义都不该外泄）。
    return {
        "items": [_clean(_chunk_row(r)) for r in kept],
        "text": "\n".join(f"[依据 #{r.id}｜{r.heading_path}]\n{r.content or ''}" for r in kept),
    }


def _check_quote(quote: str, ctx: ToolContext = None) -> dict:
    """校验一段引用是否真的在库中（复用 `citation.verify_quote`，同一个判定）。

    **存在的意义是"让模型能自检"**：与其在生成完之后被动拦截，
    不如给模型一个主动核对的工具 —— 它可以在引用前先问一句"这段原文在不在库里"。
    判定与最终闸门**同一个实现**，否则自检通过而闸门拦下，模型会无所适从。
    """
    from .citation import STATUS_EXACT, STATUS_NORMALIZED, verify_quote
    from .kb_retrieval import load_chunks_for_scope

    chunks = load_chunks_for_scope(ctx.db, ctx.scope)
    chk = verify_quote(quote, chunks, settings.citation_min_quote_chars)
    ok = chk.status in (STATUS_EXACT, STATUS_NORMALIZED)
    return {
        "items": [{"status": chk.status, "chunk_id": chk.chunk_id}] if ok else [],
        "text": (
            f"已找到该原文（{'逐字一致' if chk.status == STATUS_EXACT else '标点/空白差异后一致'}），"
            f"切片 #{chk.chunk_id}"
            if ok
            else "**未找到该原文**：这段话不在知识库里，不要作为依据引用。"
        ),
        "note": chk.status,
    }


SEARCH_KB = ToolSpec(
    name="search_kb",
    description="在知识库（官方考纲/法条 + 本人上传资料）中做相关度检索。不知道内容在哪时用它。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词或问题，尽量用考点原词"},
            "k": {"type": "integer", "description": "返回条数", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
    handler=_search_kb,
)

LOOKUP_LAW = ToolSpec(
    name="lookup_law",
    description=(
        "按「法名 + 条号」精确取法条原文（如 law=教师法, article=第七条）。"
        "问题里出现具体条款号时优先用它 —— 它比检索准，且结果确定。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "law": {"type": "string", "description": "法名，如 教师法 / 未成年人保护法"},
            "article": {"type": "string", "description": "条号，如 第七条 / 第三十七条"},
        },
    },
    handler=_lookup_law,
)

CHECK_QUOTE = ToolSpec(
    name="check_quote",
    description="核对一段原文是否真的存在于知识库中。引用前用它自检，避免编造依据。",
    parameters={
        "type": "object",
        "properties": {"quote": {"type": "string", "description": "要核对的原文片段"}},
        "required": ["quote"],
    },
    handler=_check_quote,
)

#: 注册表：**有序**，提示词里的顺序即此处顺序（让模型看到稳定的工具清单）
TOOLS: tuple[ToolSpec, ...] = (SEARCH_KB, LOOKUP_LAW, CHECK_QUOTE)
TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}

#: 回答动作（不是工具，但模型在决策里可以选择它）
ANSWER = "answer"


def tool_blocks() -> str:
    """给提示词的完整工具说明。"""
    return "\n".join(t.as_prompt_block() for t in TOOLS)


def observe(name: str, result: dict) -> dict:
    """把工具结果整理成一条"观察"，供模型在下一轮看到。"""
    return {
        "tool": name,
        "ok": bool(result.get("ok")),
        "text": result.get("text") or "",
        "error": result.get("error") or "",
        "items": result.get("items") or [],
    }


def decide(
    client,
    question: str,
    observations: list[dict] | None,
    max_calls: int,
) -> dict | None:
    """让模型选下一步动作（结构化文本契约，见模块 docstring）。

    返回 `{"tool", "args", "reason"}`；**解析失败或模型不可用返回 None** ——
    调用方按"决策不可用"降级（通常退化为直接检索一次），而不是让整个问答失败。
    """
    from .prompts_teacher import parse_tool_decision, tool_decision_prompt

    text = client.ask(tool_decision_prompt(question, observations, tool_blocks(), max_calls))
    if not text:
        return None
    return parse_tool_decision(text)
