"""取题接口：给「主观题作答与批改」页抽一道**官方池**主观题。

## 为什么需要它

主观题页原先要用户**自己把题目粘进来** —— 因为没有任何「按题型列题」的接口，
而 `MarkIn` 本来就要求 `stem`。这能用，但不是产品该有的样子：
用户的核心诉求是"我写完想立刻知道扣在哪"，题目理应来自题库。

## 三道过滤，每一道都对应一条已记录的教训

| 过滤 | 排掉什么 | 为什么必须排 |
| --- | --- | --- |
| `owner_candidate_id IS NULL` | 个人题 | ADR-0006：个人题**永不进入官方题目池**；取到别人的题既是泄漏也是错配 |
| `proofread_status == PASSED` | `PENDING` / `REJECTED` | **`PENDING` 会被抽中**是已记录的缺陷（抽题只过滤 `!= REJECTED`）—— 用户把未审校的 AI 题当练习对象、还被批改，风险比"内部抽到"更高 |
| `source_kind != seed_template` | 占位模板假题 | 占位题**无学科内容**（本地演示与测试夹具专用）。它就是"占位假题已在伤害用户"那条记录的同一个坑 |

## 不返回 `answer` / `explanation`

主观题要用户**先作答**，返回参考答案等于剧透。批改结果里已经给了扣分缘由与改进建议，
那才是这份数据该出现的位置。**这是产品判断，不是遗漏** —— 若将来要做"做完可对答案"，
应新增一个显式的「看参考答案」接口，而不是默认带出来。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as ORMSession

from ..db import get_db
from ..deps import get_current_candidate
from ..models import (
    SOURCE_KIND_SEED_TEMPLATE,
    Candidate,
    ProofreadStatus,
    Question,
    QuestionType,
)
from ..services.llm_client import LLMClient, get_llm_client
from ..services.qt import DEFAULT_QTYPE, SUBJECTIVE_QTYPES

router = APIRouter(prefix="/api/questions")

#: 可练习的主观题型 —— **唯一真相在 `services/qt.py`**。
#: 此前这里与 `routers/marking.py` 各写一份，结果后者多一个 `default`
#: 而小程序的「其它主观题」标签恰好发 `default` → 那个标签点抽题必然 400。
PRACTICE_QTYPES: tuple[str, ...] = SUBJECTIVE_QTYPES

#: 表示"随便给我一道主观题"（小程序的「其它主观题」标签用它）。它不是题型本身。
ANY_SUBJECTIVE = DEFAULT_QTYPE

#: 回传的题干上限。与 `marking.MAX_ANSWER` 同量级 —— 用户能提交的作答有多长，
#: 题干就多长；再长的材料应走「按资料出题」那条路，而不是塞进练习接口。
MAX_STEM_CHARS = 2000


class PracticeQuestionOut(BaseModel):
    id: int
    qtype: str
    module: str
    knowledge_point: str
    stem: str
    #: 客观题才有选项；主观题为空数组（前端不必分支）
    options: list[dict] = []
    aigc_flag: bool = True


@router.get("/practice", response_model=PracticeQuestionOut)
def practice_question(
    qtype: str = Query("material", description="主观题型"),
    _c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
) -> PracticeQuestionOut:
    """随机取一道可练习的官方主观题。取不到时 **404**（而不是回一个空壳题）。

    `func.random()` 是仓库既有的抽题写法（`routers/sessions.py` 的 `_sample_pool`）——
    万级题库时应在 SQL 层随机，而不是全表拉进内存再洗牌。
    """
    if qtype not in PRACTICE_QTYPES and qtype != ANY_SUBJECTIVE:
        raise HTTPException(400, f"不支持练习的题型：{qtype}")

    # `default` 的语义是「随便一道主观题」—— 小程序的「其它主观题」标签就用它。
    # ⚠️ 此前这里直接 400（因为它不在可抽题型里），用户看到的是"这个标签坏了"。
    wanted = list(PRACTICE_QTYPES) if qtype == ANY_SUBJECTIVE else [qtype]

    stmt = (
        select(Question)
        .where(
            Question.owner_candidate_id.is_(None),          # 官方池
            Question.type.in_([QuestionType(t) for t in wanted]),
            Question.proofread_status == ProofreadStatus.PASSED,  # 只取已审校
            # `source_kind` 可能是 NULL（早期 418 题没有这个列值）→ 必须 NULL 安全：
            # `!= 'seed_template'` 单独写会让 NULL 行被 SQL 三值逻辑排除掉。
            # 用 `is_distinct_from` 更直白，但它在 SQLite 上不保证可用，故用 or_ 展开。
            or_(
                Question.source_kind.is_(None),
                Question.source_kind != SOURCE_KIND_SEED_TEMPLATE,
            ),
            Question.stem != "",
        )
        .order_by(func.random())
        .limit(1)
    )
    q = db.execute(stmt).scalars().first()
    if q is None:
        raise HTTPException(
            404,
            f"题库里暂无「{qtype}」主观题（需官方池、已审校、非占位题）。"
            "题库中的主观题需由 AI 按考纲出题生成，当前还没有可用的题目；"
            "你可以把题目粘进题干框直接作答并批改。",
        )

    return PracticeQuestionOut(
        id=q.id,
        qtype=q.type.value,
        module=q.module.value if q.module else "",
        knowledge_point=q.knowledge_point or "",
        stem=(q.stem or "")[:MAX_STEM_CHARS],
        options=_parse_options(q.options),
        aigc_flag=bool(q.aigc_flag),
    )


def get_llm_client_dep() -> LLMClient:
    """可覆写的 LLM 客户端依赖（与 `teacher.py` / `marking.py` 同一约定：测试注入 fake）。"""
    return get_llm_client()


class GenerateSubjectiveIn(BaseModel):
    qtype: str = "material"


class GeneratedSubjectiveOut(BaseModel):
    """AI 现出的主观题（**不落库**）。"""

    qtype: str
    label: str
    score: int
    stem: str
    #: 出题依据（官方语料切片）—— 前端展示"这题依据什么出的"
    basis: list[dict] = []
    aigc_flag: bool = True
    #: True = 来自 AI 题池（复用）；False = 本次**新生成**（已落池，下次会复用）
    reused: bool = False
    #: 来源说明：AI 现出、**不是真题**。前端必须显示，否则用户会当成真题
    note: str = ""


@router.post("/generate-subjective", response_model=GeneratedSubjectiveOut)
def generate_subjective_question(
    body: GenerateSubjectiveIn,
    _c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client_dep),
) -> GeneratedSubjectiveOut:
    """按题型**现出一道主观题**（RAG for Generation 的主观题分支）。

    为什么不是"从题库抽"：题库里主观题 **0 条** —— 按 ADR-0003 真题原文不入库，
    而既有出题链路只产选择题，主观题从来没有被生产过（见 `services/subjective_gen.py`）。

    ⚠️ 生成失败返回 **502**（而不是 500）：那是"上游模型没产出合格结果"，可重试；
    前端据此提示"再点一次"，与"代码崩了"分开。
    """
    # 「其它主观题」标签发 `default`，语义是"随便给我出一道" —— 落到最标准的材料分析题。
    qtype = "material" if body.qtype == ANY_SUBJECTIVE else body.qtype
    if qtype not in PRACTICE_QTYPES:
        raise HTTPException(400, f"不支持生成的主观题型：{body.qtype}")

    from ..services import subjective_gen

    out = subjective_gen.generate_subjective(db, qtype, llm)
    if out.get("error"):
        raise HTTPException(502, out["error"])
    return GeneratedSubjectiveOut(**out)


def _parse_options(raw: str | None) -> list[dict]:
    """`options` 列存的是 JSON 文本；脏数据不抛异常（主观题本就多为空）。"""
    import json

    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []
