"""每日任务与考期倒计时（票 10 / Implementation 7 / 用户故事 #34-#38）。

- 考期为锚：考生手动输入考期，倒计时 = 考期 - 今天（内测阶段手动输入）。
- 每日任务：错题复习（来自票 09 错题本，按错次降序取前 5）+ 新题（池中随机 5 题）。
- 任务当日有效时限 12 小时，超时未算完成；完成后给明确反馈（成就感）。
- 考期输入接入内容安全检测调用点（真实拦截在票 13 接 msgSecCheck）。
- 严禁与分享绑定（Implementation 30）。
"""

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, DailyTask, MistakeBook, Question, QuestionSource
from .streak import advance_on_daily_complete
from ..schemas import (
    DailyCompleteIn,
    DailyCompleteOut,
    DailyOut,
    DailyTaskItems,
    DailyTaskOut,
    ExamDateIn,
    ExamDateOut,
    QuestionOut,
)
from ..services import get_content_safety
from ..services.sampling import random_rows

router = APIRouter(prefix="/api/daily", tags=["daily"])

MISTAKE_REVIEW_LIMIT = 5
NEW_QUESTIONS_LIMIT = 5
TASK_VALID_HOURS = 12


def _countdown(exam_date: str | None) -> int | None:
    if not exam_date:
        return None
    try:
        d = date.fromisoformat(exam_date)
    except ValueError:
        return None
    return max(0, (d - date.today()).days)


@router.post("/exam-date", response_model=ExamDateOut)
def set_exam_date(
    body: ExamDateIn,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> ExamDateOut:
    """输入考期（以考期为时间基准），接入内容安全输入检测调用点（验收 1/5）。"""
    try:
        exam_day = date.fromisoformat(body.exam_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="考期格式应为 YYYY-MM-DD")

    if exam_day < date.today():
        raise HTTPException(status_code=400, detail="考期不能早于今天")

    if not get_content_safety().check_input(body.exam_date):
        raise HTTPException(status_code=400, detail="输入内容未通过安全检测")

    c.exam_date = body.exam_date
    db.commit()
    return ExamDateOut(exam_date=c.exam_date, countdown_days=_countdown(c.exam_date) or 0)


def _required_ids(items: dict) -> list:
    """今日任务要求作答的全部题目 id（错题复习 + 新题）。"""
    ids = [int(m["question_id"]) for m in (items.get("mistake_review") or [])]
    ids += [int(i) for i in (items.get("new_questions") or [])]
    return ids


def mark_task_progress(db: Session, candidate_id: int, question_ids) -> None:
    """把已作答题目记入今日任务进度（幂等）。由提交闯关局时调用；不在此处 commit。"""
    if not question_ids:
        return
    today = date.today().isoformat()
    task = (
        db.query(DailyTask)
        .filter(DailyTask.candidate_id == candidate_id, DailyTask.task_date == today)
        .first()
    )
    if task is None:
        return
    done = set(json.loads(task.done_ids or "[]"))
    done.update(int(i) for i in question_ids)
    task.done_ids = json.dumps(sorted(done))
    db.add(task)


def _get_or_create_task(db: Session, candidate_id: int) -> DailyTask:
    """当日任务幂等生成：同一考生同一天只有一条任务。"""
    today = date.today().isoformat()
    task = (
        db.query(DailyTask)
        .filter(DailyTask.candidate_id == candidate_id, DailyTask.task_date == today)
        .first()
    )
    if task is not None:
        return task

    # 错题复习：来自票 09 错题本，按错次降序取前 N
    mistake_rows = (
        db.query(MistakeBook, Question.stem)
        .join(Question, Question.id == MistakeBook.question_id)
        .filter(MistakeBook.candidate_id == candidate_id)
        .order_by(MistakeBook.wrong_count.desc())
        .limit(MISTAKE_REVIEW_LIMIT)
        .all()
    )
    mistake_review = [
        {
            "question_id": mb.question_id,
            "stem": stem,
            "knowledge_point": mb.knowledge_point,
            "wrong_count": mb.wrong_count,
        }
        for mb, stem in mistake_rows
    ]

    # 新题：池中随机 N 题（排除本轮已选错题）。
    #
    # ⚠️ 这里原先与 `sessions._sample_pool` **改造前**是同一个写法：`.all()` 把全表拉进内存
    # 再 `random.shuffle`。`sessions.py` 那处已按计划改成 SQL 层随机抽样，**这处漏了同类的一处** ——
    # 418 题无碍，万级题库时每次生成当日任务都要把全表读进进程。
    # 现在两处都收口到 `services.sampling.random_rows`：只回真正需要的行。
    exclude_ids = [m["question_id"] for m in mistake_review]
    new_questions = random_rows(
        db, Question, [Question.source == QuestionSource.POOL], NEW_QUESTIONS_LIMIT, exclude_ids
    )

    task = DailyTask(
        candidate_id=candidate_id,
        exam_date=db.get(Candidate, candidate_id).exam_date,
        task_date=today,
        valid_hours=TASK_VALID_HOURS,
        items=json.dumps(
            {
                "mistake_review": mistake_review,
                "new_questions": [q.id for q in new_questions],
            },
            ensure_ascii=False,
        ),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _task_out(task: DailyTask, db: Session) -> DailyTaskOut:
    items = json.loads(task.items or "{}")
    qids = items.get("new_questions", [])
    qs = db.query(Question).filter(Question.id.in_(qids)).all() if qids else []
    by_id = {q.id: q for q in qs}
    ordered = [by_id[i] for i in qids if i in by_id]
    deadline = None
    created = task.created_at
    if created is not None:
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        deadline = (created + timedelta(hours=task.valid_hours)).isoformat()

    # 完成进度：用于校验"是否真的做过题"，避免直接点完成
    required = _required_ids(items)
    done = set(json.loads(task.done_ids or "[]"))
    required_count = len(required)
    done_count = sum(1 for i in required if i in done)

    return DailyTaskOut(
        task_id=task.id,
        task_date=task.task_date,
        valid_hours=task.valid_hours,
        completed=task.completed,
        feedback=None,
        deadline=deadline,
        items=DailyTaskItems(
            mistake_review=items.get("mistake_review", []),
            new_questions=[QuestionOut.model_validate(q) for q in ordered],
        ),
        required_count=required_count,
        done_count=done_count,
        can_complete=done_count >= required_count,
    )


@router.get("", response_model=DailyOut)
def get_daily(
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> DailyOut:
    """获取今日任务与考期倒计时（Implementation 7）。"""
    task = _get_or_create_task(db, c.id)
    return DailyOut(
        exam_date=c.exam_date,
        countdown_days=_countdown(c.exam_date),
        task=_task_out(task, db),
    )


@router.post("/complete", response_model=DailyCompleteOut)
def complete_task(
    body: DailyCompleteIn,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> DailyCompleteOut:
    """完成任务：12 小时时限内有效，超时未算完成（验收 3/4）。"""
    task = db.get(DailyTask, body.task_id)
    if task is None or task.candidate_id != c.id:
        raise HTTPException(status_code=404, detail="task not found")

    created = task.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created is not None and datetime.now(timezone.utc) > created + timedelta(
        hours=task.valid_hours
    ):
        raise HTTPException(status_code=410, detail="任务已超时（12 小时时限），未算完成")

    # 逻辑校验：必须先真的做完任务题，才能标记完成
    required = _required_ids(json.loads(task.items or "{}"))
    done = set(json.loads(task.done_ids or "[]"))
    missing = [i for i in required if i not in done]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"还有 {len(missing)} 道任务题未完成，做完后才能标记完成",
        )

    task.completed = True
    db.commit()

    # S1：推进连胜。连胜是锦上添花，失败不得阻塞每日任务完成，故单独提交并兜底。
    try:
        advance_on_daily_complete(db, c.id)
        db.commit()
    except Exception:
        db.rollback()

    days = _countdown(c.exam_date)
    feedback = (
        f"今日任务完成！距离考试还有 {days} 天，继续保持节奏。"
        if days is not None
        else "今日任务完成！继续保持节奏。"
    )
    return DailyCompleteOut(task_id=task.id, completed=True, feedback=feedback)
