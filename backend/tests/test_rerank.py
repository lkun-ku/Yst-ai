"""精排：契约、降级、以及"接上了没有"。

**为什么单列一组用例**：精排是本项目唯一一个**新增外部依赖**的能力
（ONNX 运行时 + 266MB 权重），它有三种降级路径（`off` / 权重缺失 / 加载失败）
和一种"接上了"路径。这些路径**失败时都不报错**，只是安静地退化成不重排 ——
于是"精排有效"这件事会永远无法证伪。所以把它们逐条钉成断言。

**真实模型不在单测里跑**（266MB 下载 + 每次前向），收益由
`eval/retrieval_eval.py --rerank` 测量；这里只验证**链路与降级**。
"""

import pytest

from app.config import settings
from app.services.kb_retrieval import _apply_rerank, retrieve
from app.services.rerank import FakeReranker, OnnxReranker, _passage_text, get_reranker
from app.models import Candidate, Document, DocumentChunk
from app.services.scope import NAMESPACE_PERSONAL, Scope


# ---------------- 契约：候选文本怎么拼 ----------------

def test_候选文本默认含标题_且可截断():
    """默认含标题是**实测决定**：只喂正文时 recall@1 反而低于不精排（0.5630 vs 0.7111）。

    原因是法名与条号只出现在 heading 里 —— 只喂正文时模型分不清
    「教师法第七条」与「未成年人保护法第七条」。
    """
    c = {"content": "第七条 教师享有下列权利", "heading_path": "教师法 / 第二章 / 第七条"}
    assert "教师法" in _passage_text(c, True, 512)
    assert "教师法" not in _passage_text(c, False, 512)
    assert len(_passage_text(c, True, 12)) == 12


# ---------------- FakeReranker：必须能真的改变顺序 ----------------

def test_替身是确定性的():
    a = FakeReranker()
    b = FakeReranker()
    cands = [{"content": "甲乙丙丁戊"}, {"content": "甲乙"}, {"content": "丙丁"}]
    assert [c["content"] for c in a.rerank("甲乙", cands, 3)] == [
        c["content"] for c in b.rerank("甲乙", cands, 3)
    ]


def test_替身的排序与命中数口径不同():
    """这条防的是"替身与粗排同序"——那样链路测试就是空的。

    「命中比例」与稀疏通道的命中数分子相同、分母是常数 → 排序完全一致；
    Jaccard 多了"候选有多长"这一维，排序才会真的不同。
    """
    from app.services.kb_retrieval import keyword_rank

    chunks = [{"content": "甲乙丙丁戊己庚辛壬癸"}, {"content": "甲乙丙丁"}]
    # 命中数：两片都命中 3 个查询词项 → 同分，按原顺序 → 长的在前
    assert keyword_rank("甲乙丙丁", chunks)[0][1] == 0
    # 替身（Jaccard）：短的那片更"纯" → 短的在前
    got = FakeReranker(include_heading=False).rerank("甲乙丙丁", chunks, 2)
    assert got[0]["content"] == "甲乙丙丁"
    assert "rerank_score" in got[0]


def test_替身尊重_top_k_与空输入():
    r = FakeReranker(include_heading=False)
    assert r.rerank("甲乙", [{"content": "甲乙"}, {"content": "丙丁"}], 1) == [
        {**{"content": "甲乙"}, "rerank_score": 1.0}
    ]
    assert r.rerank("甲乙", [], 5) == []


# ---------------- 工厂与降级 ----------------

def test_工厂_off_返回None表示不重排(monkeypatch):
    monkeypatch.setattr(settings, "rerank_impl", "off")
    assert get_reranker() is None


def test_工厂_fake_返回替身(monkeypatch):
    monkeypatch.setattr(settings, "rerank_impl", "fake")
    rk = get_reranker()
    assert isinstance(rk, FakeReranker) and rk.available()


def test_工厂_onnx_权重缺失时不是可用状态(monkeypatch, tmp_path):
    """**权重缺失不自动换成 fake** —— 生产里"用假模型假装精排过"比"不精排"更糟，
    它会让所有精度数字失去意义。缺失由 status() 如实报出。"""
    monkeypatch.setattr(settings, "rerank_impl", "onnx")
    monkeypatch.setattr(settings, "rerank_model_dir", str(tmp_path / "不存在"))
    rk = get_reranker()
    assert isinstance(rk, OnnxReranker)
    assert rk.available() is False
    assert "缺失" in rk.status()


def test_权重目录存在但文件损坏时_fail_open_返回原顺序(tmp_path):
    """把损坏当异常抛出会让"精排坏了"升级成"检索坏了" —— 精排是增强不是依赖。"""
    (tmp_path / "model_int8.onnx").write_bytes(b"not-an-onnx-model")
    (tmp_path / "tokenizer.json").write_bytes(b"{}")
    rk = OnnxReranker(model_dir=tmp_path)
    assert rk.available() is True  # 文件在 → 但加载会失败
    cands = [{"content": "甲"}, {"content": "乙"}]
    out = rk.rerank("甲", cands, 2)
    assert [c["content"] for c in out] == ["甲", "乙"]  # 原顺序，未被改动
    assert rk.last_error, "降级原因必须留下痕迹，否则'精排生效过'无法证伪"


# ---------------- _apply_rerank：三种状态的行为 ----------------

def test_不重排时只截断_行为与接入前一致():
    """`RERANK_IMPL=off` 必须与接入精排**逐字节一致** ——
    否则"开启精排带来的差异"里就混进了别的东西，无法归因。"""
    cands = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    assert _apply_rerank(None, "任意", cands, 2) == cands[:2]


def test_精排不可用时退回召回顺序并只记日志():
    class Broken:
        name = "broken"

        def available(self):
            return False

        def status(self):
            return "broken：故意不可用"

        def rerank(self, *a, **k):  # pragma: no cover - 不应被调用
            raise AssertionError("不可用时不该调用 rerank()")

    cands = [{"content": "a"}, {"content": "b"}]
    assert _apply_rerank(Broken(), "任意", cands, 2) == cands


def test_精排可用时真的改变顺序():
    rk = FakeReranker(include_heading=False)
    cands = [{"content": "甲乙丙丁戊己庚辛"}, {"content": "甲乙丙丁"}]
    out = _apply_rerank(rk, "甲乙丙丁", cands, 2)
    assert out[0]["content"] == "甲乙丙丁"


# ---------------- 接进 retrieve()（链路是否真的接上了）----------------

def _mk_doc(db, cid: int, contents: list[str]) -> None:
    doc = Document(
        candidate_id=cid,
        is_official=False,
        title="构造语料",
        file_type="txt",
        char_count=sum(len(c) for c in contents),
        chunk_count=len(contents),
        status="parsed",
        storage_path="构造语料",
    )
    db.add(doc)
    db.flush()
    for seq, content in enumerate(contents):
        db.add(
            DocumentChunk(
                document_id=doc.id,
                seq=seq,
                content=content,
                heading_path="无标题",  # 与查询无重叠，避免标题加成干扰本用例
                char_count=len(content),
            )
        )
    db.commit()


def test_retrieve_在重排关闭时保持召回顺序(db_session, monkeypatch):
    cid = 7801
    db_session.add(Candidate(id=cid, unionid="rk7801"))
    _mk_doc(db_session, cid, ["甲乙丙丁戊己庚辛壬癸", "甲乙丙丁"])
    monkeypatch.setattr(settings, "rerank_impl", "off")

    got = retrieve(
        db_session, "甲乙丙丁",
        Scope(namespace=NAMESPACE_PERSONAL, candidate_id=cid),
        k=2, embed_fn=lambda _q: None,
    )
    assert got[0]["content"] == "甲乙丙丁戊己庚辛壬癸"
    assert got[0]["rerank_score"] is None


def test_retrieve_在重排开启时被真的重排(db_session, monkeypatch):
    """这条是"链路接上了"的证明：同一份语料、同一个查询，只是把精排打开，
    顺序就必须改变。少了这条断言，`_apply_rerank` 没接进 `retrieve()` 也测不出来。"""
    cid = 7802
    db_session.add(Candidate(id=cid, unionid="rk7802"))
    _mk_doc(db_session, cid, ["甲乙丙丁戊己庚辛壬癸", "甲乙丙丁"])
    monkeypatch.setattr(settings, "rerank_impl", "fake")

    got = retrieve(
        db_session, "甲乙丙丁",
        Scope(namespace=NAMESPACE_PERSONAL, candidate_id=cid),
        k=2, embed_fn=lambda _q: None,
    )
    assert got[0]["content"] == "甲乙丙丁"
    assert got[0]["rerank_score"] is not None


def test_开启精排会加深召回池(db_session, monkeypatch):
    """精排只能重排已有候选，所以开了精排必须**多召回** ——
    否则池深还是 k，精排无从下手（收益上限 = recall@pool）。"""
    cid = 7803
    db_session.add(Candidate(id=cid, unionid="rk7803"))
    _mk_doc(db_session, cid, ["甲乙%d" % i for i in range(10)])
    monkeypatch.setattr(settings, "rerank_impl", "fake")
    monkeypatch.setattr(settings, "rerank_pool", 8)

    got = retrieve(
        db_session, "甲乙",
        Scope(namespace=NAMESPACE_PERSONAL, candidate_id=cid),
        k=3, embed_fn=lambda _q: None,
    )
    assert len(got) == 3


@pytest.mark.parametrize("impl", ["off", "fake"])
def test_两种实现都不抛异常(impl, db_session, monkeypatch):
    cid = 7804 + (1 if impl == "fake" else 0)
    db_session.add(Candidate(id=cid, unionid="rk%d" % cid))
    _mk_doc(db_session, cid, ["甲乙丙丁", "戊己庚辛"])
    monkeypatch.setattr(settings, "rerank_impl", impl)
    got = retrieve(
        db_session, "甲乙",
        Scope(namespace=NAMESPACE_PERSONAL, candidate_id=cid),
        k=2, embed_fn=lambda _q: None,
    )
    assert got
