"""工单 13 接缝测试：KB 任务持久化（DB 表替代模块级内存字典）+ 考生隔离。

接缝点（TDD 锁定）：
1. 任务状态落 `KbTask` 表 → 跨 Session / 跨进程可读（内存字典实现在此必然丢失）
2. `candidate_id` 归属校验 → 防越权读他人任务
3. 回归护栏：模块级 `_tasks` 内存字典必须消失
"""
import json

from app.db import SessionLocal, get_db
from app.deps import get_current_candidate
from app.main import app
from app.models import Candidate, KbTask
from app.routers import kb

CAND_A = 9201  # 唯一子树，避免与其它测试数据冲突
CAND_B = 9202
TASK_PREFIX = "t13-"


def _override(cand_id: int) -> None:
    """覆盖鉴权与 DB 依赖，指向当前测试库。"""
    app.dependency_overrides[get_current_candidate] = lambda: Candidate(id=cand_id)

    def _db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        db.query(KbTask).filter(KbTask.task_id.like(f"{TASK_PREFIX}%")).delete()
        db.commit()
    finally:
        db.close()


def test_任务进度落库且跨Session可读():
    """接缝：任务状态必须落 KbTask 表，跨 Session 可读（内存字典在此必然丢失）。"""
    tid = f"{TASK_PREFIX}persist"
    db1 = SessionLocal()
    try:
        db1.add(
            KbTask(task_id=tid, candidate_id=CAND_A, status="running", done=2, total=5)
        )
        db1.commit()
    finally:
        db1.close()

    try:
        # 全新 session（模拟新请求 / 新 worker）：内存字典实现在此必然读不到
        db2 = SessionLocal()
        try:
            rec = db2.get(KbTask, tid)
            assert rec is not None, "任务未落库：疑似退回内存字典实现"
            assert rec.status == "running"
            assert rec.done == 2
            assert rec.total == 5
        finally:
            db2.close()
    finally:
        _cleanup()


def test_他人任务返回404(client):
    """接缝：candidate_id 归属校验，防越权读他人任务。"""
    tid = f"{TASK_PREFIX}isolate"
    db = SessionLocal()
    try:
        db.add(KbTask(task_id=tid, candidate_id=CAND_A, status="done"))
        db.commit()
    finally:
        db.close()

    try:
        _override(CAND_B)  # 以另一考生身份读 A 的任务
        try:
            r = client.get(f"/api/kb/task/{tid}")
            assert r.status_code == 404
        finally:
            _clear_overrides()
    finally:
        _cleanup()


def test_任务接口返回进度与题数(client):
    """接缝：GET /api/kb/task/{id} 读 KbTask 的进度与题数。"""
    tid = f"{TASK_PREFIX}done"
    db = SessionLocal()
    try:
        db.add(
            KbTask(
                task_id=tid,
                candidate_id=CAND_A,
                status="done",
                done=3,
                total=3,
                count=3,
                generated_question_ids=json.dumps([101, 102, 103]),
            )
        )
        db.commit()
    finally:
        db.close()

    try:
        _override(CAND_A)
        try:
            r = client.get(f"/api/kb/task/{tid}")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "done"
            assert body["done"] == 3
            assert body["total"] == 3
            assert body["count"] == 3
        finally:
            _clear_overrides()
    finally:
        _cleanup()


def test_模块级内存字典已移除():
    """回归护栏：KB 任务不得再有模块级 _tasks 内存字典（重启即丢）。"""
    assert not hasattr(kb, "_tasks"), (
        "kb 模块仍有 _tasks 内存字典，任务状态会随进程重启丢失"
    )
