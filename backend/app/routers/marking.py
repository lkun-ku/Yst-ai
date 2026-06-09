"""主观题批改路由：**RAG for Evaluation** 的对外入口。

两个端点分工明确：

| 端点 | 作用 | 成本 |
| --- | --- | --- |
| `POST /api/marking/evaluate` | 批改一份作答，给四维度分 + 扣分缘由 + 改进建议 + 依据 | 1 次 LLM |
| `POST /api/marking/consistency` | 同一作答独立批改 N 次，输出各维度标准差与一致率 | **N 次 LLM** |

**一致性度量单列一个端点而不是默认附带**：它是"给 AI 批改不可信一个数字"的手段，
是**测量**动作，不是每次批改都需要。挂在默认路径上会让每次批改都三倍成本。

## 对外表述的三条纪律（与 services/marking.py 一致）

1. 四维度（切题度/论据/结构/语言）是**产品自定**的评分维度，**不是官方口径**；
2. 主观题官方评分细则**从未公开**，所以**不承诺与官方评分一致**；
3. 不接 LLM 就没有批改 —— 这是 RAG for Evaluation，不是可规则化的判分。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as ORMSession

from ..config import settings
from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate
from ..services.llm_client import LLMClient, get_llm_client
from ..services.qt import MARKING_QTYPES
from ..services.scope import NAMESPACE_BOTH, NAMESPACE_PERSONAL, Scope

router = APIRouter(prefix="/api/marking")

#: 与 chat.py 的 MAX_INPUT 对齐：主观题作答本就更长，给 2000 字上限
MAX_ANSWER = 2000
#: 一致性度量的次数上限：防止一句请求把成本放大几十倍
MAX_VOTES = 5
#: 可批改的题型 —— **唯一真相在 `services/qt.py`**（比可抽题的清单多一个 `default`：
#: 用户自己粘贴题目时可能说不清题型）。此前这里与 `routers/questions.py` 各写一份，
#: 两边差一个 `default`，导致小程序「其它主观题」标签抽题必然 400。
QTYPES = MARKING_QTYPES


def get_llm_client_dep() -> LLMClient:
    return get_llm_client()


class MarkIn(BaseModel):
    stem: str
    answer: str
    qtype: str = "material"
    include_official: bool = True


class ConsistencyIn(MarkIn):
    n: int = 3


class CitationOut(BaseModel):
    quote: str


class RubricItemOut(BaseModel):
    # ⚠️ **id 必须是 `int | str`，不能只写 int**：批改依据现在有两类来源 ——
    # 官方切片是整数 id，而答案库的**规则条目**是字符串 id（`rule-material` / `rule-writing`，
    # 见 `data/answer_bank/评分规则.json`）。只写 `int` 会让响应模型校验失败，
    # 整个 `/api/marking/evaluate` 直接 **500**（实测踩到：单测全绿，因为单测不经过响应模型）。
    id: int | str | None = None
    heading_path: str | None = None
    content: str = ""
    #: 权威级别（官方 / 答案库·半官方）—— 前端要能如实展示"这条依据什么来头"
    authority: str = ""


class MarkOut(BaseModel):
    qtype: str
    dimensions: dict[str, float]
    total: float
    comments: dict[str, str]
    deductions: list[str]
    suggestions: list[str]
    citations: list[CitationOut]
    grounded: bool
    refused: bool
    refusal_reason: str
    rubric: list[RubricItemOut]
    #: 必须随结果一起返回：四维度是产品自定口径，不是官方评分维度
    dimensions_disclaimer: str = "四维度为本产品自定的结构化反馈口径，非官方评分维度"


class ConsistencyOut(BaseModel):
    n: int
    per_dim_std: dict[str, float]
    total_std: float
    mean_total: float
    agreement: float
    mean_dimensions: dict[str, float]


def _validate(body: MarkIn) -> tuple[str, str]:
    stem = (body.stem or "").strip()
    answer = (body.answer or "").strip()
    if not stem:
        raise HTTPException(400, "题目不能为空")
    if not answer:
        raise HTTPException(400, "作答不能为空")
    if len(answer) > MAX_ANSWER:
        raise HTTPException(400, f"作答请控制在 {MAX_ANSWER} 字以内")
    if body.qtype not in QTYPES:
        raise HTTPException(400, "无效题型")
    return stem, answer


def _scope(c: Candidate, include_official: bool) -> Scope:
    return Scope(
        namespace=NAMESPACE_BOTH if include_official else NAMESPACE_PERSONAL,
        candidate_id=c.id,
    )


@router.post("/evaluate", response_model=MarkOut)
def evaluate(
    body: MarkIn,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client_dep),
) -> MarkOut:
    stem, answer = _validate(body)
    from ..services.marking import mark_answer, retrieve_rubric

    scope = _scope(c, body.include_official)
    rubric = retrieve_rubric(db, scope, body.qtype, k=settings.marking_rubric_k)
    result = mark_answer(llm, stem, answer, rubric)
    out = result.as_dict()
    return MarkOut(
        qtype=body.qtype,
        dimensions=out["dimensions"],
        total=out["total"],
        comments=out["comments"],
        deductions=out["deductions"],
        suggestions=out["suggestions"],
        citations=[CitationOut(**x) for x in out["citations"]],
        grounded=out["grounded"],
        refused=out["refused"],
        refusal_reason=out["refusal_reason"],
        rubric=[
            RubricItemOut(
                id=r.get("id"),
                heading_path=r.get("heading_path"),
                content=str(r.get("content") or "")[:500],
                authority=r.get("authority") or "",
            )
            for r in rubric
        ],
    )


@router.post("/consistency", response_model=ConsistencyOut)
def consistency(
    body: ConsistencyIn,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client_dep),
) -> ConsistencyOut:
    stem, answer = _validate(body)
    if body.n < 2 or body.n > MAX_VOTES:
        raise HTTPException(400, f"独立批改次数应在 2~{MAX_VOTES} 之间")
    from ..services.marking import measure_consistency, retrieve_rubric

    scope = _scope(c, body.include_official)
    rubric = retrieve_rubric(db, scope, body.qtype, k=settings.marking_rubric_k)
    if not rubric:
        # 没有依据时**不做一致性度量**：它会返回 n=0，而那与"批改很稳"看起来一样，
        # 容易被误读成好结果。直接 409 让调用方先解决依据问题。
        raise HTTPException(409, "没有可引用的评分依据，无法度量批改一致性")

    r = measure_consistency(llm, stem, answer, rubric, n=body.n)
    if r.n == 0:
        raise HTTPException(503, "批改全部失败，无法度量一致性")

    # 四维度均分用于前端画分项条：`measure_consistency` 只回标准差，
    # 这里再批一次取分数（调用方明知这是 N+1 次成本，故单列一个端点）
    from ..services.marking import mark_answer

    one = mark_answer(llm, stem, answer, rubric)
    return ConsistencyOut(
        n=r.n,
        per_dim_std=r.per_dim_std,
        total_std=r.total_std,
        mean_total=r.mean_total,
        agreement=r.agreement,
        mean_dimensions=one.dimensions if one.grounded else {},
    )
