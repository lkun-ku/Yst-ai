"""随机抽样收口的测试。

**为什么测"SQL 里有 LIMIT"而不是只测行数**：只测行数的话，`.all()` 之后再切片**也能通过** ——
而那样"改造"就只是在原地换个写法，代价一点没变。这一处的病根正是"看起来改了、其实没改"，
所以断言要落在**编译出的 SQL** 上。

⚠️ **隔离粒度必须是"用例"，不是"文件"**：会话内数据库共享，
本文件第一版把全部用例都写在同一个考点名下，于是**同文件里前面的用例留下的行**
会把后面用例的计数打红（我从"id 连续"改到"按考点过滤"，仍红 —— 就是这个原因）。
现在每个用例由 `kp` fixture 拿一个唯一考点名，只认自己造的行。
这与 `conftest` 那句"测试必须与仓库数据无关"是同一条纪律，只是"外部数据"换成了同会话的其它用例。

构造题目本身照抄仓库既有约定（`options`/`answer` 存 **JSON 字符串**、题型参数是 `type=`）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.models import Module, Question, QuestionSource, QuestionType  # noqa: E402
from app.services.sampling import random_query, random_rows  # noqa: E402

_OPTS = [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}]


@pytest.fixture
def kp(request) -> str:
    """本用例专用的考点名（唯一）—— 见文件 docstring 的隔离说明。"""
    return f"抽样测试-{request.node.name}"


def _question(module: Module, stem: str, kp: str) -> Question:
    return Question(
        module=module,
        knowledge_point=kp,
        stem=stem,
        options=json.dumps(_OPTS, ensure_ascii=False),
        answer=json.dumps(["A"]),
        explanation="解析",
        type=QuestionType.SINGLE,
        source=QuestionSource.POOL,
    )


def _filters(kp: str) -> list:
    return [Question.knowledge_point == kp]


def _mine(db, kp: str) -> list[Question]:
    return db.query(Question).filter(Question.knowledge_point == kp).all()


# ---------------- 结构性断言：不许再退化成"全表拉取" ----------------


def test_查询必须带_LIMIT(db_session, kp):
    """核心守卫：`.all()` + 内存切片能让行数测试通过，但 SQL 里不会有 LIMIT。"""
    for n in (1, 5, 50):
        sql = str(random_query(db_session, Question, _filters(kp), n))
        assert "LIMIT" in sql.upper(), f"n={n} 的查询里没有 LIMIT：{sql}"


def test_排除条件进到_SQL_里而不是取回后再过滤(db_session, kp):
    sql = str(random_query(db_session, Question, _filters(kp), 5, exclude_ids=[1, 2, 3])).upper()
    assert "NOT IN" in sql, f"排除应当在 SQL 里完成（否则等于把全表读回来再筛）：{sql}"


# ---------------- 行为断言 ----------------


def test_最多返回n行(db_session, kp):
    for i in range(30):
        db_session.add(_question(Module.PROFESSIONAL_IDEA, f"题目{i}", kp))
    db_session.commit()

    assert len(random_rows(db_session, Question, _filters(kp), 5)) == 5
    assert len(random_rows(db_session, Question, _filters(kp), 1)) == 1
    # 题库不足时按实际数量返回，而不是报错
    assert len(random_rows(db_session, Question, _filters(kp), 999)) == 30


def test_n为零或负数时返回空且不发查询(db_session, kp):
    assert random_rows(db_session, Question, _filters(kp), 0) == []
    assert random_rows(db_session, Question, _filters(kp), -3) == []


def test_过滤条件生效(db_session, kp):
    kp_a, kp_b = f"{kp}-甲", f"{kp}-乙"
    for i in range(6):
        db_session.add(_question(Module.PROFESSIONAL_IDEA, f"甲{i}", kp_a))
    for i in range(6):
        db_session.add(_question(Module.EDU_LAW, f"乙{i}", kp_b))
    db_session.commit()

    got = random_rows(db_session, Question, _filters(kp_b), 10)
    assert len(got) == 6 and all(q.knowledge_point == kp_b for q in got)


def test_排除指定id(db_session, kp):
    for i in range(10):
        db_session.add(_question(Module.PROFESSIONAL_IDEA, f"题目{i}", kp))
    db_session.commit()
    all_ids = [q.id for q in _mine(db_session, kp)]

    got = random_rows(db_session, Question, _filters(kp), 10, exclude_ids=all_ids[:6])
    assert {q.id for q in got} == set(all_ids[6:])


def test_排除全部时返回空而不是报错(db_session, kp):
    for i in range(3):
        db_session.add(_question(Module.PROFESSIONAL_IDEA, f"题目{i}", kp))
    db_session.commit()
    all_ids = [q.id for q in _mine(db_session, kp)]
    assert random_rows(db_session, Question, _filters(kp), 5, exclude_ids=all_ids) == []


def test_确实在随机而不是每次同一批(db_session, kp):
    """30 题里每次取 5，取若干次应当出现**不止一种**结果。

    不要求某一次一定不同（概率上可能重复），只要求多次里出现差异 ——
    否则说明 `RANDOM()` 没生效（例如被别的排序覆盖了）。
    """
    for i in range(30):
        db_session.add(_question(Module.PROFESSIONAL_IDEA, f"题目{i}", kp))
    db_session.commit()

    seen = {
        tuple(sorted(q.id for q in random_rows(db_session, Question, _filters(kp), 5)))
        for _ in range(12)
    }
    assert len(seen) > 1, "12 次抽样的结果完全相同，随机性可疑"
