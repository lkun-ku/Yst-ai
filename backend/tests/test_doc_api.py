"""AI 出题端到端集成测试：上传 → 解析切分 → 生成 → 轮询 → 个人题库闯关。

用 fake LLM，不耗额度；验证后台线程（独立 DB Session，A6）能正确落库。
真实模式（用户决策 2026-04-09）下生成 30s+，10s 轮询窗口必超时——生成链路的
真实行为由题库管线（418 题真生成）验证，此处 fake 语义跳过。
"""
import io
import time

import pytest

from app.config import settings

_skip_real = pytest.mark.skipif(
    settings.llm_mode == "real",
    reason="真实模式下生成超测试轮询窗口（30s+ > 10s），fake 语义测试跳过",
)

BODY = (
    "第一章 教育基础\n"
    + "教育的本质是培养人的社会活动，这是教育区别于其他社会活动的根本特征。" * 40
    + "\n第二章 教学原理\n"
    + "教学原则是根据教育目的和教学规律制定的指导教学工作的基本要求。" * 40
)


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


@_skip_real
def test_upload_parse_generate_and_practice(client):
    uid = _guest(client)
    h = {"X-Unionid": uid}

    # 1) 上传并解析
    files = {"file": ("讲义.txt", io.BytesIO(BODY.encode("utf-8")), "text/plain")}
    r = client.post("/api/documents", files=files, headers=h)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["chunk_count"] >= 2  # 两章应被切成多片
    assert doc["char_count"] > 0

    # 2) 资料详情返回章节树（供出题页选「知识点范围」）
    r = client.get(f"/api/documents/{doc['id']}", headers=h)
    assert r.status_code == 200
    assert "第一章 教育基础" in r.json()["headings"]

    # 3) 提交生成（整卷模式：章节配额）
    r = client.post(
        f"/api/documents/{doc['id']}/generate",
        json={
            "mode": "paper",
            "spec": [{"type": "single", "count": 4}, {"type": "blank", "count": 2}],
            "difficulty": "medium",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # 4) 轮询等待后台线程完成
    for _ in range(100):
        t = client.get(f"/api/tasks/{task_id}", headers=h).json()
        if t["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert t["status"] == "done", t
    assert t["question_count"] > 0

    # 5) 生成结果归入个人题库，且不计入官方池
    qs = client.post(
        "/api/sessions/start", json={"doc_id": doc["id"], "question_count": 3}, headers=h
    )
    assert qs.status_code == 200, qs.text
    questions = qs.json()["questions"]
    assert len(questions) == 3
    assert all(q["module"] == "个人资料" for q in questions)

    # 6) 个人题闯关不消耗每日额度（B3）
    quota = client.get("/api/quota", headers=h).json()
    assert quota["used_today"] == 0

    # 7) 删除资料级联移除个人题（A5）
    r = client.delete(f"/api/documents/{doc['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["removed_questions"] > 0
    qs = client.post("/api/sessions/start", json={"doc_id": doc["id"], "question_count": 1}, headers=h)
    assert qs.status_code == 404


@_skip_real
def test_generated_count_matches_requested(client):
    """回归：实际出题数必须等于用户选择题量。

    曾因整卷模式把批次切了两次（build_batches 已切 + 按段再切），
    同一段同一题型被拆成多批，各批 LLM 都从"变式1"编号 → 重复被去重丢弃 → 出题数不足。
    """
    uid = _guest(client)
    h = {"X-Unionid": uid}
    files = {"file": ("讲义.txt", io.BytesIO(BODY.encode("utf-8")), "text/plain")}
    doc = client.post("/api/documents", files=files, headers=h).json()

    spec = [{"type": "single", "count": 8}, {"type": "judge", "count": 4}]
    r = client.post(
        f"/api/documents/{doc['id']}/generate",
        json={"mode": "paper", "spec": spec},
        headers=h,
    )
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]

    for _ in range(100):
        t = client.get(f"/api/tasks/{tid}", headers=h).json()
        if t["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert t["status"] == "done", t
    assert t["question_count"] == 12, f"请求 12 题，实际生成 {t['question_count']} 题"


@_skip_real
def test_generated_count_matches_requested_spot_mode(client):
    """定点模式同样必须题数一致。"""
    uid = _guest(client)
    h = {"X-Unionid": uid}
    files = {"file": ("讲义.txt", io.BytesIO(BODY.encode("utf-8")), "text/plain")}
    doc = client.post("/api/documents", files=files, headers=h).json()

    r = client.post(
        f"/api/documents/{doc['id']}/generate",
        json={"mode": "spot", "spec": [{"type": "blank", "count": 9}], "focus": "教育"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]

    for _ in range(100):
        t = client.get(f"/api/tasks/{tid}", headers=h).json()
        if t["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert t["status"] == "done", t
    assert t["question_count"] == 9, f"请求 9 题，实际生成 {t['question_count']} 题"


def test_upload_rejects_unsupported_type(client):
    uid = _guest(client)
    files = {"file": ("a.exe", io.BytesIO(b"x"), "application/octet-stream")}
    r = client.post("/api/documents", files=files, headers={"X-Unionid": uid})
    assert r.status_code == 400
    assert "不支持的文件类型" in r.json()["detail"]


def test_generate_rejects_empty_spec(client):
    uid = _guest(client)
    h = {"X-Unionid": uid}
    files = {"file": ("讲义.txt", io.BytesIO(BODY.encode("utf-8")), "text/plain")}
    doc = client.post("/api/documents", files=files, headers=h).json()

    r = client.post(f"/api/documents/{doc['id']}/generate", json={"spec": []}, headers=h)
    assert r.status_code == 400
    assert "至少指定一种题型" in r.json()["detail"]


def test_other_user_cannot_access_document(client):
    uid_a = _guest(client)
    uid_b = client.post("/api/identity/guest").json()["unionid"]
    files = {"file": ("讲义.txt", io.BytesIO(BODY.encode("utf-8")), "text/plain")}
    doc = client.post("/api/documents", files=files, headers={"X-Unionid": uid_a}).json()

    # 他人不可见、不可出题、不可删
    assert client.get(f"/api/documents/{doc['id']}", headers={"X-Unionid": uid_b}).status_code == 404
    assert (
        client.post(
            f"/api/documents/{doc['id']}/generate",
            json={"spec": [{"type": "single", "count": 1}]},
            headers={"X-Unionid": uid_b},
        ).status_code
        == 404
    )
    assert client.delete(f"/api/documents/{doc['id']}", headers={"X-Unionid": uid_b}).status_code == 404
