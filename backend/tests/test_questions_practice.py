"""取练习题的接口：**三道过滤各自都要有断言**。

这个接口会被用户直接拿来做练习并送去批改，所以它排掉的三类题源不是"优化"而是"红线"：
① 未审校的 `PENDING`（已记录：它会被抽中）；② 占位模板假题（已记录：无学科内容却混在官方池里）；
③ 他人的个人题（ADR-0006：个人题永不进官方池）。

断言写法刻意**不依赖库里只有我播的题**：本仓测试库是会话级重建、不逐用例回滚，
别的用例可能已经插了合格题目 —— 所以我断言的是"**被排除的标记一次都没出现**"，
而不是"取到的一定是我那条"。前者在共享库里依然成立，后者会随机失败。
"""
import pytest

from app.deps import get_current_candidate
from app.main import app
from app.models import (
    SOURCE_KIND_SEED_TEMPLATE,
    Candidate,
    Module,
    ProofreadStatus,
    Question,
    QuestionType,
)

#: 本文件专用标记：用它做删除范围，**只清自己那份**（清空全表会动到别的用例的夹具）。
MARK = "ZZQ练习接口"
CAND = 8821
OTHER = 8822

#: 被排除的四类各自的标记。抽到了任意一个都说明对应那道过滤失效。
EXCLUDED = {
    "待审": "【待审】",
    "占位": "【占位】",
    "他人": "【他人】",
    "单选": "【单选】",
}
AVAILABLE = "【可取】"

#: 抽样次数：单次抽样即使过滤漏了也可能"恰好"抽到别条；多抽几次让漏网概率降到可忽略。
DRAWS = 15


def _cleanup(db) -> None:
    db.query(Question).filter(Question.knowledge_point == MARK).delete(synchronize_session=False)
    db.commit()


def _seed(db) -> None:
    _cleanup(db)
    if db.get(Candidate, OTHER) is None:
        db.add(Candidate(id=OTHER, unionid="practice8822"))
    base = dict(
        module=Module.EDU_LAW,
        knowledge_point=MARK,
        options="[]",
        answer='["参考答案"]',
        explanation="解析",
        type=QuestionType.MATERIAL,
        proofread_status=ProofreadStatus.PASSED,
        owner_candidate_id=None,
        source_kind=None,
    )
    rows = [
        (f"{AVAILABLE}{MARK}", {}),
        (f"{EXCLUDED['待审']}{MARK}", {"proofread_status": ProofreadStatus.PENDING}),
        (f"{EXCLUDED['占位']}{MARK}", {"source_kind": SOURCE_KIND_SEED_TEMPLATE}),
        (f"{EXCLUDED['他人']}{MARK}", {"owner_candidate_id": OTHER}),
        (f"{EXCLUDED['单选']}{MARK}", {"type": QuestionType.SINGLE}),
        (f"【写作可取】{MARK}", {"type": QuestionType.WRITING}),
        ("", {}),  # 空题干：也不该被取到
    ]
    for stem, over in rows:
        db.add(Question(stem=stem, **{**base, **over}))
    db.commit()


@pytest.fixture
def practice_client(client, db_session):
    _seed(db_session)
    app.dependency_overrides[get_current_candidate] = lambda: Candidate(id=CAND)
    yield client
    app.dependency_overrides.clear()
    _cleanup(db_session)


def _draws(client, qtype: str = "material", n: int = DRAWS) -> list[dict]:
    """抽 n 次（每次都是一次真实请求，抽样来自 SQL 的 random()）。"""
    out = []
    for _ in range(n):
        r = client.get(f"/api/questions/practice?qtype={qtype}")
        assert r.status_code == 200, r.text
        out.append(r.json())
    return out


def test_返回体形状且不含参考答案(practice_client):
    """**不返回 `answer` / `explanation`** 是产品判断（先作答、别剧透），不是遗漏。"""
    body = _draws(practice_client, n=1)[0]
    assert set(body) == {"id", "qtype", "module", "knowledge_point", "stem", "options", "aigc_flag"}
    assert "answer" not in body and "explanation" not in body
    assert body["qtype"] == "material"
    assert body["stem"]


@pytest.mark.parametrize("label", sorted(EXCLUDED))
def test_被排除的题一次都没被抽到(practice_client, label: str):
    """四类排除各自有断言 —— 过滤条件写错一条，只有对应的那条会红。"""
    marker = EXCLUDED[label]
    for body in _draws(practice_client):
        assert marker not in body["stem"], f"{label}类题目泄漏到了练习接口：{body['stem'][:60]}"


def test_空题干不会被抽到(practice_client):
    for body in _draws(practice_client):
        assert body["stem"].strip(), "抽到了空题干"


def test_题型区分(practice_client):
    """`qtype` 必须真的生效，而不是恒定返回同一个池子。"""
    for body in _draws(practice_client, qtype="writing", n=3):
        assert body["qtype"] == "writing"


def test_不支持练习的题型被拒(practice_client):
    """客观题不走这条路 —— 它不需要"作答 + 批改"的流程。"""
    assert practice_client.get("/api/questions/practice?qtype=single").status_code == 400
    assert practice_client.get("/api/questions/practice?qtype=nonsense").status_code == 400


def test_无匹配时返回404而不是空壳题(practice_client, db_session):
    """题库里没有可练习的题时，明确 404 —— 回一道空题会让前端渲染出空白卡。

    ⚠️ 只有在库里确实没有该类合格题时才可断言：本仓测试库是会话级共享的，
    别的用例可能已经插了 `design` 题。条件不成立时**跳过并说明**，而不是假装通过。
    """
    n = (
        db_session.query(Question)
        .filter(
            Question.owner_candidate_id.is_(None),
            Question.type == QuestionType.DESIGN,
            Question.proofread_status == ProofreadStatus.PASSED,
            Question.stem != "",
        )
        .count()
    )
    if n:
        pytest.skip(f"库里有 {n} 道合格的 design 题（其它用例插入），无法测空态")
    r = practice_client.get("/api/questions/practice?qtype=design")
    assert r.status_code == 404
    assert "主观题" in r.json()["detail"]
