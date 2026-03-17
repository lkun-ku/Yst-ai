"""工单 15 交付接口测试：GET /api/kb/task/{task_id}/questions。

覆盖验收要求的四类场景：
1. 正常交付：按 `generated_question_ids` 顺序返回，带 `source_chunk` 溯源
2. 鉴权：他人任务 → 404（不得越权读题）
3. 不存在任务 → 404
4. 空结果：任务无题目 → 返回 []

数据隔离：题目 id 用自增（不硬编码，避免与其它测试子树冲突）；
Candidate/KbTask 用专属 id 段，且每个用例前后都做幂等清理。
"""
import json

from app.db import SessionLocal, get_db
from app.deps import get_current_candidate
from app.main import app
from app.models import (
    Candidate,
    KbTask,
    Module,
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
)
from app.schemas import KbQuestionOut

CAND_A = 9301  # 唯一子树
CAND_B = 9302
TASK_PREFIX = "t15-"


def _cleanup() -> None:
    """幂等清理：按 FK 顺序删（Question → KbTask → Candidate）。"""
    db = SessionLocal()
    try:
        db.query(Question).filter(
            Question.owner_candidate_id.in_([CAND_A, CAND_B])
        ).delete(synchronize_session=False)
        db.query(KbTask).filter(KbTask.task_id.like(f"{TASK_PREFIX}%")).delete()
        db.query(Candidate).filter(Candidate.id.in_([CAND_A, CAND_B])).delete()
        db.commit()
    finally:
        db.close()


def _seed(cand_id: int, n: int, with_chunk: bool = True) -> tuple[str, list[int]]:
    """建 Candidate + n 道题 + KbTask，返回 (task_id, 题目 id 列表)。"""
    _cleanup()  # 先清，保证可重复运行
    db = SessionLocal()
    try:
        db.add(Candidate(id=cand_id, unionid=f"u{cand_id}"))
        db.flush()

        qids: list[int] = []
        for i in range(n):
            q = Question(
                module=Module.PERSONAL,
                knowledge_point="测试考点",
                stem=f"题目{i}",
                options=json.dumps(
                    [{"key": "A", "text": "选项A"}, {"key": "B", "text": "选项B"}],
                    ensure_ascii=False,
                ),
                answer=json.dumps(["A"]),
                explanation="解析",
                type=QuestionType.SINGLE,
                source=QuestionSource.DOC,
                proofread_status=ProofreadStatus.PENDING,
                aigc_flag=True,
                version=1,
                owner_candidate_id=cand_id,
                source_chunk=f"依据切片{i}" if with_chunk else None,
            )
            db.add(q)
            db.flush()  # 拿到自增 id
            qids.append(q.id)

        tid = f"{TASK_PREFIX}{cand_id}"
        db.add(
            KbTask(
                task_id=tid,
                candidate_id=cand_id,
                status="done",
                count=n,
                generated_question_ids=json.dumps(qids),
            )
        )
        db.commit()
        return tid, qids
    finally:
        db.close()


def _override(cand_id: int) -> None:
    app.dependency_overrides[get_current_candidate] = lambda: Candidate(id=cand_id)

    def _db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db


def test_交付按生成顺序返回并带溯源(client):
    """正常交付：按 generated_question_ids 顺序返回，含 source_chunk 溯源。"""
    tid, qids = _seed(CAND_A, 3)
    try:
        _override(CAND_A)
        try:
            r = client.get(f"/api/kb/task/{tid}/questions")
            assert r.status_code == 200
            body = r.json()
            assert [q["id"] for q in body] == qids, "未按生成顺序返回"
            assert all(q["source_chunk"] for q in body), "缺 source_chunk 溯源"
            assert "source_chunk" in KbQuestionOut.model_fields
        finally:
            app.dependency_overrides.clear()
    finally:
        _cleanup()


def test_他人任务交付返回404(client):
    """鉴权：他人任务不得越权读题。"""
    tid, _ = _seed(CAND_A, 1)
    try:
        _override(CAND_B)
        try:
            r = client.get(f"/api/kb/task/{tid}/questions")
            assert r.status_code == 404
        finally:
            app.dependency_overrides.clear()
    finally:
        _cleanup()


def test_不存在任务返回404(client):
    _override(CAND_A)
    try:
        r = client.get("/api/kb/task/does-not-exist/questions")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_空结果返回空列表(client):
    """任务存在但无题目 → []，不报错。"""
    tid, _ = _seed(CAND_A, 0)
    try:
        _override(CAND_A)
        try:
            r = client.get(f"/api/kb/task/{tid}/questions")
            assert r.status_code == 200
            assert r.json() == []
        finally:
            app.dependency_overrides.clear()
    finally:
        _cleanup()
