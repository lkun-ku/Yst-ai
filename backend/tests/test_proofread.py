"""票 13：题目纠错与审校队列 + 内容安全真实接入。"""

import json

from app.models import (
    ErrorReport,
    Module,
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
)
from app.seed.import_questions import import_questions
from app.seed.questions_data import KNOWLEDGE_POINTS
from app.services.proofread import sample_pending_questions
from app.services.validation import validate_question_payload


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


ADMIN = {"X-Admin-Token": "dev-admin"}

VALID_KPS = {kp for kps in KNOWLEDGE_POINTS.values() for kp in kps}


# ---------- 结构化校验（Implementation 19-20，不通过即弃） ----------

def _valid_payload() -> dict:
    return {
        "module": "职业理念",
        "knowledge_point": "学生观",
        "stem": "题干",
        "options": [
            {"key": "A", "text": "选项A"},
            {"key": "B", "text": "选项B"},
        ],
        "answer": ["A"],
        "explanation": "解析文本",
        "type": "single",
    }


def test_validate_question_ok():
    assert validate_question_payload(_valid_payload(), VALID_KPS) == []


def test_validate_question_failures():
    d = _valid_payload()
    del d["stem"]
    assert any("stem" in e for e in validate_question_payload(d, VALID_KPS))  # schema

    d = _valid_payload()
    d["options"][1]["key"] = "A"
    assert any("选项" in e for e in validate_question_payload(d, VALID_KPS))  # 选项互斥

    d = _valid_payload()
    d["answer"] = ["C"]
    assert any("答案" in e for e in validate_question_payload(d, VALID_KPS))  # 答案必须命中选项

    d = _valid_payload()
    d["answer"] = ["A", "A"]
    assert any("答案" in e for e in validate_question_payload(d, VALID_KPS))  # 答案唯一

    d = _valid_payload()
    d["explanation"] = "  "
    assert any("解析" in e for e in validate_question_payload(d, VALID_KPS))  # 解析非空

    d = _valid_payload()
    d["knowledge_point"] = "不存在的考点"
    assert any("考点" in e for e in validate_question_payload(d, VALID_KPS))  # 考点归属存在


# ---------- 抽检比例（文化素养最高，配置可调） ----------

def test_sampling_ratio_culture_highest(db_session):
    # 清空共享库中的待审池，保证比例断言只受本测试数据影响
    db_session.query(Question).filter(Question.proofread_status == ProofreadStatus.PENDING).delete()
    db_session.commit()
    for m in Module:
        for i in range(10):
            db_session.add(
                Question(
                    module=m,
                    knowledge_point=f"考点{i}",
                    stem=f"{m.value}{i}",
                    options=json.dumps([{"key": "A", "text": "a"}]),
                    answer=json.dumps(["A"]),
                    explanation="解析",
                    type=QuestionType.SINGLE,
                    source=QuestionSource.POOL,
                    proofread_status=ProofreadStatus.PENDING,
                )
            )
    db_session.commit()

    sampled = sample_pending_questions(db_session)
    by_module: dict[str, int] = {}
    for q in sampled:
        by_module[str(q.module.value)] = by_module.get(str(q.module.value), 0) + 1

    assert by_module["文化素养"] == 3  # 0.3 * 10
    assert by_module["职业理念"] == 1  # 默认 0.1 * 10
    assert by_module["文化素养"] > by_module["职业理念"]


# ---------- 最小审校后台 ----------

def _mk_question(kp: str = "学生观") -> Question:
    return Question(
        module=Module.PROFESSIONAL_IDEA,
        knowledge_point=kp,
        stem=f"测试题《{kp}》",
        options=json.dumps([{"key": "A", "text": "a"}, {"key": "B", "text": "b"}]),
        answer=json.dumps(["A"]),
        explanation="解析",
        type=QuestionType.SINGLE,
        source=QuestionSource.POOL,
        proofread_status=ProofreadStatus.PENDING,
    )


def test_admin_requires_token(client):
    assert client.get("/api/admin/proofread/pending").status_code == 403


def test_admin_pending_and_reject_discard(client, db_session):
    """待审队列 → 驳回即弃（不再进入发起闯关的池中）（Implementation 20）。"""
    db_session.query(Question).filter(Question.proofread_status == ProofreadStatus.PENDING).delete()
    db_session.commit()
    q = _mk_question("教育观·审校")
    db_session.add(q)
    db_session.commit()
    qid = q.id

    pend = client.get("/api/admin/proofread/pending", headers=ADMIN).json()
    assert any(x["id"] == qid for x in pend)

    r = client.post(f"/api/admin/proofread/{qid}/resolve", json={"approved": False}, headers=ADMIN)
    assert r.status_code == 200
    db_session.expire_all()
    assert db_session.get(Question, qid).proofread_status == ProofreadStatus.REJECTED

    r2 = client.post(
        "/api/sessions/start", json={"knowledge_point": "教育观·审校", "question_count": 1},
        headers={"X-Unionid": _guest(client)},
    )
    assert r2.status_code == 409  # 驳回即弃：无可选题


def test_admin_approve(client, db_session):
    q = _mk_question("教师观·审校")
    db_session.add(q)
    db_session.commit()
    r = client.post(f"/api/admin/proofread/{q.id}/resolve", json={"approved": True}, headers=ADMIN)
    assert r.status_code == 200
    db_session.expire_all()
    assert db_session.get(Question, q.id).proofread_status == ProofreadStatus.PASSED


# ---------- 纠错提交与错误报告处理 ----------

def test_error_report_flow(client, db_session):
    """纠错入口提交进入审校队列；最小后台通过/驳回处理（验收 1/2）。"""
    import_questions(db_session)
    uid = _guest(client)
    qid = db_session.query(Question).first().id

    r = client.post(
        "/api/reports",
        json={"question_id": qid, "error_type": "answer", "detail": "这道题答案应为 B"},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 200

    queue = client.get("/api/admin/reports", params={"status": "pending"}, headers=ADMIN).json()
    assert any(x["id"] == r.json()["report_id"] for x in queue)

    rid = r.json()["report_id"]
    r2 = client.post(f"/api/admin/reports/{rid}/resolve", json={"action": "accept"}, headers=ADMIN)
    assert r2.status_code == 200
    db_session.expire_all()
    assert db_session.get(ErrorReport, rid).status == "resolved"


def test_error_report_invalid_type(client, db_session):
    import_questions(db_session)
    uid = _guest(client)
    qid = db_session.query(Question).first().id
    r = client.post(
        "/api/reports",
        json={"question_id": qid, "error_type": "other", "detail": "x"},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 400


def test_error_report_unsafe_detail_rejected(client, db_session, monkeypatch):
    """内容安全：考生输入侧检测，未通过不得入库（Implementation 29）。"""
    from app.routers import reports as reports_router

    class RejectClient:
        def check_input(self, text: str) -> bool:
            return False

    monkeypatch.setattr(reports_router, "get_content_safety", lambda: RejectClient())
    import_questions(db_session)
    uid = _guest(client)
    qid = db_session.query(Question).first().id
    r = client.post(
        "/api/reports",
        json={"question_id": qid, "error_type": "answer", "detail": "违规文本"},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 400


def test_error_report_requires_identity(client):
    r = client.post("/api/reports", json={"question_id": 1, "error_type": "answer", "detail": "x"})
    assert r.status_code == 401


# ---------- 内容安全：输出侧（复盘报告段落） ----------

def test_review_output_safety_fallback(client, db_session, monkeypatch):
    """输出侧检测未通过时不得展示原内容，降级为占位文案（Implementation 29）。"""
    from app.routers import review as review_router

    class RejectOutput:
        def check_input(self, text: str) -> bool:
            return True

        def check_output(self, text: str) -> bool:
            return False

    monkeypatch.setattr(review_router, "get_content_safety", lambda: RejectOutput())
    import_questions(db_session)
    uid = _guest(client)
    r = client.post(
        "/api/sessions/start", json={"module": "职业理念", "question_count": 3},
        headers={"X-Unionid": uid},
    )
    sid = r.json()["session_id"]
    answers = [
        {"question_id": q["id"], "selected": json.loads(q["answer"])} for q in r.json()["questions"]
    ]
    client.post(
        "/api/sessions/submit", json={"session_id": sid, "answers": answers},
        headers={"X-Unionid": uid},
    )
    rev = client.get(f"/api/review/{sid}", headers={"X-Unionid": uid}).json()
    assert "未通过" in rev["paragraph"]
