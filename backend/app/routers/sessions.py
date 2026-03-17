import json
import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..routers.quota import enforce_quota
from ..models import (
    Candidate,
    Mastery,
    MistakeBook,
    Module,
    ProofreadStatus,
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
    SessionHistoryOut,
    SessionStartIn,
    SessionStartOut,
    SubmitIn,
    SubmitOut,
)
from ..services.validation import normalize_answer

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# ---------- S3 模考 ----------
MOCK_QUESTION_COUNT = 30
MOCK_DURATION_SEC = 45 * 60

#: 官方模块权重（%）：职业理念15 / 职业道德15 / 教育法律法规10 / 文化素养12 / 基本能力48
MOCK_WEIGHTS = (
    (Module.PROFESSIONAL_IDEA, 15),
    (Module.PROFESSIONAL_ETHICS, 15),
    (Module.EDU_LAW, 10),
    (Module.CULTURE_LITERACY, 12),
    (Module.BASIC_ABILITY, 48),
)


def _iso_utc(dt) -> str | None:
    """统一输出带 UTC 偏移的 ISO 串。SQLite 读回的 datetime 会丢失 tzinfo，需补回，
    否则前端拿到的截止时间没有时区信息，可能按本地时区误判。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _select_by_weight(db, n: int) -> list:
    """按官方模块权重抽题，尽量贴近真实卷面分布；题库不足时再补齐。"""
    out: list = []
    picked: set = set()
    for m, w in MOCK_WEIGHTS:
        need = max(1, round(n * w / 100))
        rows = (
            db.query(Question)
            .filter(
                Question.source == QuestionSource.POOL,
                Question.module == m,
                Question.proofread_status != ProofreadStatus.REJECTED,
            )
            .all()
        )
        if not rows:
            continue
        random.shuffle(rows)
        for q in rows[:need]:
            if q.id not in picked:
                out.append(q)
                picked.add(q.id)

    if len(out) < n:
        extra = (
            db.query(Question)
            .filter(
                Question.source == QuestionSource.POOL,
                Question.proofread_status != ProofreadStatus.REJECTED,
            )
            .all()
        )
        random.shuffle(extra)
        for q in extra:
            if len(out) >= n:
                break
            if q.id not in picked:
                out.append(q)
                picked.add(q.id)

    random.shuffle(out)
    return out[:n]


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
    """开始闯关局：按模块或考点发起，一局固定题量一次下发（Implementation 4）。

    免费考生受每日 20 题额度约束（票 12，跨局累计、按自然日重置）；VIP 不限量；
    只限速不限制内容范围（Implementation 31）。
    """
    if body.question_count <= 0:
        raise HTTPException(status_code=400, detail="question_count 必须为正整数")

    sess_mode = "normal"
    duration = 0

    if body.doc_id:
        # 个人题库分支（B3：不消耗每日额度——题目已生成，边际成本为零）
        pool = (
            db.query(Question)
            .filter(
                Question.source == QuestionSource.DOC,
                Question.owner_candidate_id == c.id,
                Question.doc_id == body.doc_id,
            )
            .all()
        )
        if not pool:
            raise HTTPException(404, "该资料下暂无题目，请先生成试题")
        # 个人题数量由用户生成量决定，不足时按实际数量开局（不报 409）
        selected = _arrange(pool, min(body.question_count, len(pool)))
        module: Module | None = Module.PERSONAL
        kp: str | None = f"资料#{body.doc_id}"
    elif body.mode == "mock":
        # S3 模考：按官方权重抽题 + 限时（截止时间为服务端绝对值，防客户端改时间）
        want = body.question_count if body.question_count > 10 else MOCK_QUESTION_COUNT
        selected = _select_by_weight(db, want)
        if not selected:
            raise HTTPException(409, "题库暂无足够题目，无法开始模考")
        module = None
        kp = None
        sess_mode = "mock"
        duration = MOCK_DURATION_SEC

    else:
        if not body.module and not body.knowledge_point:
            raise HTTPException(status_code=400, detail="module 或 knowledge_point 至少提供一个")

        enforce_quota(db, c, body.question_count)

        q = db.query(Question).filter(
            Question.source == QuestionSource.POOL,
            Question.proofread_status != ProofreadStatus.REJECTED,  # 驳回即弃（票 13）
        )
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
        module = body.module
        kp = body.knowledge_point

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=duration) if duration > 0 else None

    sess = Session(
        candidate_id=c.id,
        module=module,
        knowledge_point=kp,
        question_count=len(selected),
        question_ids=json.dumps([x.id for x in selected], ensure_ascii=False),
        mode=sess_mode,
        duration_sec=duration,
        deadline_at=deadline,
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
        mode=sess.mode,
        duration_sec=sess.duration_sec,
        deadline_at=_iso_utc(sess.deadline_at),
        server_now=_iso_utc(now),  # 供前端校正本地时钟
    )


def _judge(q: Question, selected: list[str]) -> QuestionJudgement:
    qtype = str(getattr(q.type, "value", q.type) or "single")

    # 按题型分派：填空走文本归一化匹配；简答不自动判分；其余沿用集合比较
    if qtype == "blank":
        return _judge_blank(q, selected)
    if qtype == "short":
        return _judge_short(q, selected)

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


def _load_answer_list(q: Question) -> list[str]:
    """读取标准答案（DB 中统一以 JSON 存储），容错非 JSON 的存量数据。"""
    try:
        raw = json.loads(q.answer)
    except Exception:
        return [str(q.answer or "")]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [str(raw)]


def _judge_blank(q: Question, selected: list[str]) -> QuestionJudgement:
    """填空题：可接受答案文本列表 + 归一化匹配（B2 容错）。"""
    acceptable = _load_answer_list(q)
    ok = {normalize_answer(x) for x in acceptable if str(x).strip()}
    picked = [str(s) for s in (selected or []) if str(s).strip()]
    hit = any(normalize_answer(s) in ok for s in picked)

    return QuestionJudgement(
        question_id=q.id,
        is_correct=hit,
        selected=picked,
        correct=acceptable,
        state="correct" if hit else "wrong",
        options_state=None,
        explanation=q.explanation,
        knowledge_point=q.knowledge_point,
        positive_note=(
            "答对啦，继续保持" if hit else f"别灰心，已加入错题本；考点《{q.knowledge_point}》再巩固一下"
        ),
    )


def _judge_short(q: Question, selected: list[str]) -> QuestionJudgement:
    """简答题：不自动判分（B1），标记 self_review 供前端展示自评提示。

    提交侧会跳过该题的掌握度更新与错题本写入，也不计入正确率分母。
    """
    ref = _load_answer_list(q)
    return QuestionJudgement(
        question_id=q.id,
        is_correct=False,  # 不自动判分，非"答错"
        selected=[str(s) for s in (selected or [])],
        correct=ref,
        state="self_review",
        options_state=None,
        explanation=q.explanation,
        knowledge_point=q.knowledge_point,
        positive_note="本题为简答，已记录你的作答，请对照参考答案自评",
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

        # B1：简答不自动判分 —— 不计入掌握度、不进错题本、不计入正确率分母
        if j.state == "self_review":
            continue

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

    # 记入今日任务进度：让"完成每日任务"必须真的做过题（失败不影响提交结果）
    try:
        from .daily import mark_task_progress

        mark_task_progress(db, c.id, [ans.question_id for ans in body.answers])
    except Exception:
        pass

    # S3：模考超时判定 —— 以服务端时间为准，客户端改本地时间无效
    is_timeout = False
    if sess.mode == "mock" and sess.deadline_at:
        dl = sess.deadline_at
        if dl.tzinfo is None:
            dl = dl.replace(tzinfo=timezone.utc)
        is_timeout = datetime.now(timezone.utc) > dl

    sess.status = SessionStatus.SUBMITTED
    sess.timeout = is_timeout
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


@router.get("/history", response_model=list[SessionHistoryOut])
def list_history(
    limit: int = 20,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> list[SessionHistoryOut]:
    """历史闯关局列表（票 11 / D3 / Implementation 26）：已提交局按时间倒序。

    每局含日期、模块/考点与掌握度概览（本局各模块正确率）；回看报告复用
    GET /api/review/{session_id}（票 08 结构）。未提交局不计入。
    """
    rows = (
        db.query(Session)
        .filter(Session.candidate_id == c.id, Session.status == SessionStatus.SUBMITTED)
        .order_by(Session.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )

    out: list[SessionHistoryOut] = []
    for sess in rows:
        qids = json.loads(sess.question_ids)
        qs = db.query(Question).filter(Question.id.in_(qids)).all() if qids else []
        module_by_qid = {q.id: q.module for q in qs}

        per_module_total: dict[str, int] = {}
        per_module_correct: dict[str, int] = {}
        correct_count = 0
        if sess.result_json:
            for r in json.loads(sess.result_json):
                mod = module_by_qid.get(r["question_id"])
                if mod is None:
                    continue
                key = str(mod.value)
                per_module_total[key] = per_module_total.get(key, 0) + 1
                if r.get("is_correct"):
                    per_module_correct[key] = per_module_correct.get(key, 0) + 1
                    correct_count += 1
        overview = {
            k: round(per_module_correct.get(k, 0) / v, 4)
            for k, v in per_module_total.items()
            if v > 0
        }

        out.append(
            SessionHistoryOut(
                session_id=sess.id,
                created_at=sess.created_at.isoformat() if sess.created_at else "",
                submitted_at=sess.submitted_at.isoformat() if sess.submitted_at else None,
                module=sess.module,
                knowledge_point=sess.knowledge_point,
                question_count=sess.question_count,
                correct_count=correct_count,
                mastery_overview=overview,
            )
        )
    return out


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
