"""问答老师路由：**有据答疑**（RAG for QA 的对外入口）。

## 与 `chat.py` 是两件事

| 模块 | 方向 | 解决什么 |
| --- | --- | --- |
| `chat.py` | 面试官提问 → 考生回答 → 追问 + 终评给分 | 训练"**答**" |
| 本模块 | 考生提问 → 检索考纲/法条 → 带引用作答 | 解决"**问**" |

**为什么不把 `chat.py` 改造过来**：读完全文后确认它是完整的面试训练闭环 ——
5 题制、追问链、终评落库、历史/统计、语音输入输出。
把它改成问答会**替换掉**这个功能，而不是增强它。

计划里要求「保留无据版作对照」，本模块用 `mode="plain"` 表达，
比留一个旧端点更适合做对照：**同一套提示词、同一个接缝、同一个引用校验，
唯一差异是有没有检索** —— 否则模型换代、提示词漂移都会混进差异里，测出来的东西无法归因。

## 接口

- `POST /api/teacher/ask` 提问 → 回答 + 引用 + 依据（或显式拒答）

三档 `mode`：`plain`（不检索）/ `grounded`（检索一次）/ `agent`（模型自主决定查什么）。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as ORMSession

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate
from ..services.llm_client import LLMClient, get_llm_client
from ..services.scope import NAMESPACE_BOTH, NAMESPACE_PERSONAL, Scope

router = APIRouter(prefix="/api/teacher")

#: 与 chat.py 的 MAX_INPUT 对齐：问题不需要更长，长输入只增加成本与注入面
MAX_QUESTION = 500
MODES = ("plain", "grounded", "agent")
#: 单条依据回传的字符上限：法条切片很小，考纲段落可能较长 —— 前端展示不需要全文
MAX_EVIDENCE_CHARS = 800


def get_llm_client_dep() -> LLMClient:
    """可覆写的 LLM 客户端依赖（与 chat.py 同一约定：测试注入 fake）。"""
    return get_llm_client()


class Citation(BaseModel):
    quote: str
    source: str = ""


class HistoryTurn(BaseModel):
    role: str = "user"
    content: str = ""


class TeacherAskIn(BaseModel):
    question: str
    mode: str = "grounded"
    #: 多轮：最近几轮对话。**只在非空时**才会多花一次"查询改写"调用（单轮不花）。
    #: 长度与单条字数的裁剪在 `teacher_agent._trim_history` 一处做（出口统一）。
    history: list[HistoryTurn] = []
    #: 是否把官方语料（考纲/法条）纳入检索范围。关掉即"只查我自己的资料"
    include_official: bool = True
    subject: str | None = None  # 领域包名（预留：Scope 的科目过滤尚未落到 SQL）
    stage: str | None = None


class TeacherAskOut(BaseModel):
    question: str
    mode: str
    answer: str | None
    citations: list[Citation]
    confidence: str
    refused: bool
    refusal_reason: str
    #: 无据兜底：回答来自模型通识、**没有资料佐证**。前端必须显式提示，不得与有据回答同款展示。
    ungrounded: bool = False
    notice: str = ""
    #: 多轮下**实际**拿去检索的查询（已按上下文补全指代）—— 便于排查"改写错了"
    retrieval_query: str = ""
    evidence: list[dict]
    observations: list[dict]
    tool_calls: int
    citation_report: dict


def _evidence_out(items: list[dict]) -> list[dict]:
    """依据 → 前端可用结构（截断正文，剥掉内部键）。"""
    out: list[dict] = []
    for it in items or []:
        content = str(it.get("content") or "")
        out.append(
            {
                "id": it.get("id"),
                "document_id": it.get("document_id"),
                "seq": it.get("seq"),
                "heading_path": it.get("heading_path"),
                "content": content[:MAX_EVIDENCE_CHARS],
                "truncated": len(content) > MAX_EVIDENCE_CHARS,
            }
        )
    return out


@router.post("/ask", response_model=TeacherAskOut)
def teacher_ask(
    body: TeacherAskIn,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client_dep),
) -> TeacherAskOut:
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    if len(question) > MAX_QUESTION:
        raise HTTPException(400, f"问题请控制在 {MAX_QUESTION} 字以内")
    if body.mode not in MODES:
        raise HTTPException(400, "无效的应答模式")

    from ..services.teacher_agent import ask as agent_ask

    scope = Scope(
        namespace=NAMESPACE_BOTH if body.include_official else NAMESPACE_PERSONAL,
        candidate_id=c.id,
        subject=body.subject,
        stage=body.stage,
    )
    result = agent_ask(
        db,
        c.id,
        question,
        scope,
        mode=body.mode,
        client=llm,
        history=[t.model_dump() for t in body.history],
    )
    return TeacherAskOut(
        question=question,
        mode=result["mode"],
        answer=result["answer"],
        citations=[Citation(**x) for x in result["citations"]],
        confidence=result["confidence"],
        refused=result["refused"],
        refusal_reason=result["refusal_reason"],
        ungrounded=result.get("ungrounded", False),
        notice=result.get("notice", ""),
        retrieval_query=result.get("retrieval_query", question),
        evidence=_evidence_out(result["evidence"]),
        observations=result["observations"],
        tool_calls=result["tool_calls"],
        citation_report=result["citation"],
    )
