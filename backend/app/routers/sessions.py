import json
import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import (
    Candidate,
    Mastery,
    MistakeBook,
    Question,
    QuestionSource,
    QuestionType,
    Session,
    SessionStatus,
)
from ..schemas import (
    AnswerIn,
    QuestionJudgement,
    QuestionOut,
    SessionStartIn,
    SessionStartOut,
    SubmitIn,
    SubmitOut,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _arrange(rows: list[Question], n: int) -> list[Question]:
    """随机取样并尝试避免相邻同题型（Implementation 4：不连续两个相同题型）。"""
    rows = list(rows)
    random.shuffle(rows)
    selected = rows[:n]
    for i in range(len(selected) - 1):
        if selected[i].type == selected[i + 1].type:
            for j in range(i + 2, len(selected)):
                if selected[j].type != selected[i].type:
                    selected[i + 1], selected[j] = selected[j], selected[i + 1]
                    break
    return selected


@router.post("/start", response_model=SessionStartOut)
def start_session(
    body: SessionStartIn,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> SessionStartOut:
    """开始闯关局：按模块或考点发起，一局固定题量一次下发（Implementation 4）。"""
    if not body.module and not body.knowledge_point:
        raise HTTPException(status_code=400, detail="module 或 knowledge_point 至少提供一个")
    if body.question_count <= 0:
        raise HTTPException(status_code=400, detail="question_count 必须为正整数")

    q = db.query(Question).filter(Question.source == QuestionSource.POOL)
    if body.module is not None:
        q = q.filter(Question.module == body.module)
    if body.knowledge_point is not None:
        q = q.filter(Question.knowledge_point == body.knowledge_point)

    pool = q.all()
    if len(pool) < body.question_count:
        raise HTTPException(
            status_code=409,
            detail=f"可选题目不足：需 {body.question_count}，仅 {len(pool)}",
        )

    selected = _arrange(pool, body.question_count)
    sess = Session(
        candidate_id=c.id,
        module=body.module,
        knowledge_point=body.knowledge_point,
        question_count=len(selected),
        question_ids=json.dumps([x.id for x in selected], ensure_ascii=False),
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    return SessionStartOut(
        session_id=sess.id,
        candidate_id=c.id,
        module=sess.module,
        knowledge_point=sess.knowledge_point,
        question_count=sess.question_count,
        questions=[QuestionOut.model_validate(x) for x in selected],
    )


def _judge(q: Question, selected: list[str]) -> QuestionJudgement:
    correct_set = set(json.loads(q.answer))
    selected_set = set(selected)
    is_correct = selected_set == correct_set

    options_state: dict[str, str] | None = None
    if q.type == QuestionType.MULTIPLE:
        options_state = {}
        for o in json.loads(q.options):
            k = o["key"]
            if k in correct_set and k in selected_set:
                options_state[k] = "correct_selected"
            elif k in correct_set and k not in selected_set:
                options_state[k] = "missed"
            elif k not in correct_set and k in selected_set:
                options_state[k] = "wrong_selected"
            else:
                options_state[k] = "wrong_not_selected"
        state = "correct" if is_correct else ("partial" if correct_set & selected_set else "wrong")
    else:
        state = "correct" if is_correct else "wrong"

    positive_note = "答对啦，继续保持" if is_correct else f"别灰心，已加入错题本；考点《{q.knowledge_point}》再巩固一下"

    return QuestionJudgement(
        question_id=q.id,
        is_correct=is_correct,
        selected=list(selected_set),
        correct=list(correct_set),
        state=state,
        options_state=options_state,
        explanation=q.explanation,
        knowledge_point=q.knowledge_point,
        positive_note=positive_note,
    )


@router.post("/submit", response_model=SubmitOut)
def submit_session(
    body: SubmitIn,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> SubmitOut:
    """提交闯关局：逐题判定（四态）、更新掌握度与错题本、幂等（Implementation 5）。"""
    sess = db.get(Session, body.session_id)
    if sess is None or sess.candidate_id != c.id:
        raise HTTPException(status_code=404, detail="session not found")

    # 幂等：已提交则直接返回缓存结果
    if sess.status == SessionStatus.SUBMITTED and sess.result_json:
        cached = json.loads(sess.result_json)
        return SubmitOut(
            session_id=sess.id,
            idempotent=True,
            results=[QuestionJudgement(**r) for r in cached],
            mastery=_snapshot_mastery(db, c.id),
            new_mistakes=[],
        )

    results: list[QuestionJudgement] = []
    new_mistakes: list[int] = []
    mastery_final: dict[str, float] = {}

    for ans in body.answers:
        q = db.get(Question, ans.question_id)
        if q is None:
            continue
        j = _judge(q, ans.selected)
        results.append(j)

        # 掌握度增量（EMA）
        m = (
            db.query(Mastery)
            .filter(Mastery.candidate_id == c.id, Mastery.module == q.module)
            .first()
        )
        if m is None:
            m = Mastery(candidate_id=c.id, module=q.module, score=0.0, attempts=0)
        m.score = round(m.score * 0.7 + (1.0 if j.is_correct else 0.0) * 0.3, 4)
        m.attempts += 1
        db.add(m)
        mastery_final[str(q.module.value)] = m.score

        # 错题本（按考点聚合）
        if not j.is_correct:
            mb = (
                db.query(MistakeBook)
                .filter(MistakeBook.candidate_id == c.id, MistakeBook.question_id == q.id)
                .first()
            )
            if mb is None:
                mb = MistakeBook(
                    candidate_id=c.id, question_id=q.id, knowledge_point=q.knowledge_point
                )
                new_mistakes.append(q.id)
            else:
                mb.wrong_count += 1
            db.add(mb)

    sess.status = SessionStatus.SUBMITTED
    sess.submitted_at = datetime.now(timezone.utc)
    sess.result_json = json.dumps([r.model_dump() for r in results], ensure_ascii=False)
    db.commit()

    return SubmitOut(
        session_id=sess.id,
        idempotent=False,
        results=results,
        mastery=mastery_final,
        new_mistakes=new_mistakes,
    )


def _snapshot_mastery(db: Session, candidate_id: int) -> dict[str, float]:
    return {
        str(m.module.value): m.score
        for m in db.query(Mastery).filter(Mastery.candidate_id == candidate_id).all()
    }


@router.get("/{session_id}", response_model=SessionStartOut)
def get_session(
    session_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> SessionStartOut:
    """重复拉取同一闯关局题目（续答/弱网重连，Implementation 6）。"""
    sess = db.get(Session, session_id)
    if sess is None or sess.candidate_id != c.id:
        raise HTTPException(status_code=404, detail="session not found")
    qids = json.loads(sess.question_ids)
    qs = db.query(Question).filter(Question.id.in_(qids)).all()
    by_id = {q.id: q for q in qs}
    ordered = [by_id[i] for i in qids if i in by_id]
    return SessionStartOut(
        session_id=sess.id,
        candidate_id=c.id,
        module=sess.module,
        knowledge_point=sess.knowledge_point,
        question_count=sess.question_count,
        questions=[QuestionOut.model_validate(x) for x in ordered],
    )
