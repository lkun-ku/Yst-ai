"""工单 20：端到端真实回归测试（上传资料 → 按资料+用户要求出题 → 交付）。

验证核心需求：「**用户上传资料，AI 按照资料、再根据用户输入的要求出题**」。

**默认跳过**：需真实 LLM/Embedding，有费用且耗时（约 1~2 分钟）。
显式开启：

    RUN_REAL_E2E=1 LLM_API_KEY=sk-xxx python -m pytest tests/test_e2e_kb_real.py -v

可选环境变量：
    LLM_API_BASE / LLM_MODEL（默认百炼 qwen-plus）
    EMBEDDING_API_KEY（缺省复用 LLM_API_KEY）/ EMBEDDING_MODEL（默认 text-embedding-v3）

断言只校验**结构**与**流程状态**，不断言 LLM 具体措辞（输出有随机性）。
"""
import json
import os
import time

import pytest

from app.config import settings
from app.db import SessionLocal
from app.deps import get_current_candidate
from app.main import app
from app.models import Candidate, DocumentChunk

RUN = os.getenv("RUN_REAL_E2E") == "1"
KEY = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
API_BASE = os.getenv("LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
EMBED_KEY = os.getenv("EMBEDDING_API_KEY") or KEY

pytestmark = pytest.mark.skipif(
    not (RUN and KEY),
    reason="端到端真实测试默认跳过；需 RUN_REAL_E2E=1 且提供 LLM_API_KEY（会调用真实 LLM，产生费用）",
)

CID = 71001  # 唯一子树，避免与其它测试数据冲突

# 一段自洽的知识资料：题目是否忠于它，是可判定的
DOC = """光合作用的基本概念

光合作用是绿色植物、藻类和某些细菌利用太阳光能，把二氧化碳和水转化成储存着能量的有机物，并且释放出氧气的过程。

光合作用的场所

光合作用主要在叶绿体中进行。叶绿体中的叶绿素能够吸收光能，其中叶绿素a和叶绿素b主要吸收红光和蓝紫光，对绿光吸收最少，所以叶片呈现绿色。

光合作用的过程

光合作用分为光反应和暗反应两个阶段。光反应发生在叶绿体的类囊体薄膜上，需要光照，产物包括ATP、NADPH和氧气。暗反应发生在叶绿体基质中，不需要光照，利用光反应产生的ATP和NADPH将二氧化碳固定，生成糖类等有机物。

影响光合作用的因素

光照强度、二氧化碳浓度和温度都会影响光合作用速率。在一定范围内，光照增强或二氧化碳浓度升高，光合作用速率会加快；温度通过影响酶的活性来影响光合作用。
"""


@pytest.fixture
def real_llm(monkeypatch):
    """把 LLM / Embedding 接缝切到真实供应商（仅本测试生效）。"""
    monkeypatch.setattr(settings, "llm_mode", "real")
    monkeypatch.setattr(settings, "llm_api_key", KEY)
    monkeypatch.setattr(settings, "llm_api_base", API_BASE)
    monkeypatch.setattr(settings, "llm_model", os.getenv("LLM_MODEL", "qwen-plus"))
    monkeypatch.setattr(settings, "embedding_mode", "real")
    monkeypatch.setattr(settings, "embedding_api_key", EMBED_KEY)
    monkeypatch.setattr(settings, "embedding_api_base", API_BASE)
    monkeypatch.setattr(settings, "embedding_model", os.getenv("EMBEDDING_MODEL", "text-embedding-v3"))


@pytest.fixture
def candidate(client):
    """建考生并覆盖鉴权；结束后清理本用例数据。"""
    db = SessionLocal()
    db.add(Candidate(id=CID, unionid=f"e2e{CID}"))
    db.commit()
    db.close()

    app.dependency_overrides[get_current_candidate] = lambda: Candidate(id=CID)
    try:
        yield CID
    finally:
        app.dependency_overrides.clear()
        db = SessionLocal()
        db.query(Candidate).filter(Candidate.id == CID).delete()
        db.commit()
        db.close()


def _wait_embedded(doc_id: int, timeout: int = 90) -> tuple[int, int]:
    """等待切片向量化完成，返回 (ok 数, 总数)。"""
    for _ in range(timeout):
        db = SessionLocal()
        try:
            q = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id)
            total = q.count()
            ok = q.filter(DocumentChunk.embed_status == "ok").count()
            if total and ok == total:
                return ok, total
        finally:
            db.close()
        time.sleep(1)
    return ok, total


def test_上传资料后按资料与要求出题并交付(client, real_llm, candidate):
    # ---------- 1) 上传资料 ----------
    r = client.post(
        "/api/documents",
        files={"file": ("光合作用.txt", DOC.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200, f"上传失败: {r.text[:200]}"
    doc = r.json()
    doc_id = doc["id"]
    assert doc["chunk_count"] >= 1, "未解析出切片"
    print(f"[1] 上传成功 doc_id={doc_id} 切片={doc['chunk_count']}")

    # ---------- 2) 等待向量化 ----------
    ok, total = _wait_embedded(doc_id)
    print(f"[2] 向量化 {ok}/{total}")
    assert total >= 1
    assert ok == total, f"向量化未完成: {ok}/{total}"

    # ---------- 3) 按资料 + 用户要求出题 ----------
    spec = [{"type": "single", "count": 3}]
    r = client.post(
        "/api/kb/generate",
        json={
            "scope": "光合作用",
            "spec": spec,
            "difficulty": "medium",
            "focus": "重点考察光反应与暗反应的区别",
            "route": "graph",  # 路线③ LangGraph（生产默认）
        },
    )
    assert r.status_code == 200, f"出题提交失败: {r.text[:200]}"
    task_id = r.json()["task_id"]
    print(f"[3] 出题任务 {task_id}")

    # ---------- 4) 轮询至终态 ----------
    st = {}
    for _ in range(150):
        st = client.get(f"/api/kb/task/{task_id}").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(2)
    print(f"[4] 终态 {st}")
    assert st.get("status") == "done", f"任务未成功: {st}"

    # ---------- 5) 交付 ----------
    r = client.get(f"/api/kb/task/{task_id}/questions")
    assert r.status_code == 200
    qs = r.json()
    print(f"[5] 交付题目数 {len(qs)}")

    assert len(qs) == 3, f"题数不符 spec(3)，实际 {len(qs)}"

    for i, q in enumerate(qs, 1):
        # 题型符合 spec
        assert q["type"] == "single", f"第{i}题题型应为 single，实际 {q['type']}"
        # 题干与解析非空
        assert q["stem"].strip(), f"第{i}题题干为空"
        assert q["explanation"].strip(), f"第{i}题解析为空"
        # 选项结构合法（接口返回的是 JSON 字符串，需解析）
        opts = json.loads(q["options"]) if isinstance(q["options"], str) else q["options"]
        assert len(opts) == 4, f"第{i}题选项数应为 4，实际 {len(opts)}"
        # 答案合法且在选项范围内
        ans = json.loads(q["answer"]) if isinstance(q["answer"], str) else q["answer"]
        assert ans, f"第{i}题无答案"
        assert ans[0] in {o["key"] for o in opts}, f"第{i}题答案 {ans} 不在选项内"
        # 溯源（工单 17：批级切片 id 清单）
        assert q["source_chunk"], f"第{i}题缺 source_chunk 溯源"
        print(f"    第{i}题 ok: {q['stem'][:40]}… 答案={ans}")

    # ---------- 6) 忠于资料的弱校验 ----------
    # 资料关键词应出现在题干或解析中（focus 已引导，稳健性足够）
    joined = " ".join((q["stem"] or "") + (q["explanation"] or "") for q in qs)
    assert "光反应" in joined and "暗反应" in joined, "题目未体现资料核心概念，疑似未按资料出题"
