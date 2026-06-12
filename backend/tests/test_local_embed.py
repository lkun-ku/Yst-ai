"""本地 ONNX 向量模型（`services/local_embed.py`）与其在检索链路里的接缝。

## 为什么这些测试**不加载真模型**

权重 330MB，测试套件若依赖它就会：慢、要联网下载、CI 上直接不可用。
（同一考虑见 `rerank`：`RERANK_IMPL` 在测试里默认 off。）

但**不加载模型**不等于**不测这条链路** —— 真正会出错的地方恰好都不需要权重：
池化口径写错（静默降低检索质量）、权重缺失时退成伪向量（假成功）、
来源值没被落库接住（整批语料变 failed）。这些都用替身测。
"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import embedding as emb_mod  # noqa: E402
from app.services import kb_corpus, local_embed  # noqa: E402


# ---------------- 池化口径：CLS + L2 归一化 ----------------

class _FakeTokenizer:
    def encode_batch(self, texts):
        return [
            SimpleNamespace(ids=[1, 2, 3], attention_mask=[1, 1, 1], type_ids=[0, 0, 0])
            for _ in texts
        ]


class _FakeSession:
    """两批两条、序列长 2、隐层 3 维的假前向。

    刻意让**首 token 与其它 token 不同**：[CLS] 池化取 `[1,0,0]` / `[0,3,4]`，
    平均池化会得到别的东西 —— 于是"用了哪种池化"是可判定的。

    ⚠️ 必须返回 `np.ndarray`（真实 onnxruntime 的返回类型）：先前的替身返回 Python
    嵌套 list，于是 `hidden.ndim` 直接 `AttributeError` —— **替身与真身类型不一致时，
    测的是替身而不是代码**。
    """

    def __init__(self, names: tuple[str, ...] = ("input_ids", "attention_mask", "token_type_ids")):
        self.names = names
        self.ran = 0
        self.seen_feeds: list[set[str]] = []

    def get_inputs(self):
        return [SimpleNamespace(name=n) for n in self.names]

    def run(self, _out, feeds):
        import numpy as np

        self.ran += 1
        self.seen_feeds.append(set(feeds))
        return [
            np.array(
                [
                    [[1.0, 0.0, 0.0], [9.0, 9.0, 9.0]],   # 第 1 条：CLS = [1,0,0]
                    [[0.0, 3.0, 4.0], [1.0, 1.0, 1.0]],   # 第 2 条：CLS = [0,3,4] → [0,0.6,0.8]
                ]
            )
        ]


def _stub_embedder(
    tmp_path, monkeypatch, names: tuple[str, ...] = ("input_ids", "attention_mask", "token_type_ids")
) -> local_embed.OnnxEmbedder:
    e = local_embed.OnnxEmbedder(tmp_path)
    (tmp_path / local_embed.MODEL_FILE).write_bytes(b"x" * 2048)
    (tmp_path / local_embed.TOKENIZER_FILE).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(e, "_load", lambda: None)
    e._tokenizer = _FakeTokenizer()
    e._session = _FakeSession(names)
    e._input_names = set(names)
    return e


def test_取_cls_而非均值并做_l2_归一化(tmp_path, monkeypatch):
    """BGE 的标准用法是 **`[CLS]` 池化 + L2 归一化**。

    写错池化不会报错，只会让检索质量悄悄下降（相似度算出来仍"像那么回事"）——
    所以这条断言直接钉住数值：取首 token，且模长为 1。
    """
    e = _stub_embedder(tmp_path, monkeypatch)
    vecs = e.embed(["教师法", "义务教育法"])

    assert vecs[0] == pytest.approx([1.0, 0.0, 0.0])
    assert vecs[1] == pytest.approx([0.0, 0.6, 0.8])
    for v in vecs:
        assert sum(x * x for x in v) == pytest.approx(1.0)  # 归一化后余弦 = 点积


def test_只喂模型声明要的输入(tmp_path, monkeypatch):
    """不同导出对 `token_type_ids` 要求不同，按**声明**取用。

    硬编码的失败形态是 `invalid input name`，很难一眼看出根因；这条挡住它。
    """
    names = ("input_ids", "attention_mask")  # 声明里没有 token_type_ids
    e = _stub_embedder(tmp_path, monkeypatch, names)
    e.embed(["x"])

    assert e._session.seen_feeds == [set(names)]  # 多喂一个就会在这条断言上露出来


# ---------------- 缺权重：必须显式失败，不许退成伪向量 ----------------

def test_local_模式缺权重时_strict_embed_判失败而非伪向量(monkeypatch, tmp_path):
    """**这是本模块最重要的一条**：`local` 模式最容易出的不是"调用报错"，

    而是**权重还没下**。若当成"没配"退回 64 维伪向量，库里会与"灌了真向量"一模一样
    （全是 `ok`）—— 检索看起来有向量、实际等于关键词，且无人发现。
    （同一坑在 2026-06-12 由百炼欠费踩过一次，见 `strict_embed` 的 docstring。）
    """
    monkeypatch.setattr(emb_mod.settings, "embedding_mode", "local")
    monkeypatch.setattr(emb_mod.settings, "embedding_model_dir", str(tmp_path))  # 空目录
    local_embed.reset_embedder()

    vec, source, reason = emb_mod.strict_embed("教师享有教育教学权")

    assert vec is None
    assert source == emb_mod.EMBED_FAILED
    assert "local" in reason and "权重" in reason
    local_embed.reset_embedder()


def test_local_模式缺权重时_embed_one_仍可降级(monkeypatch, tmp_path):
    """与上一条**刻意不对称**：用户上传讲义不该因为"模型还没下"而失败。

    `embed_one` 面向"上传后立刻能出题"（宁可弱一点也要跑通），
    `strict_embed` 面向"官方语料的入库台账"（宁可失败也不能假）。两者服务的目标不同。
    """
    monkeypatch.setattr(emb_mod.settings, "embedding_mode", "local")
    monkeypatch.setattr(emb_mod.settings, "embedding_model_dir", str(tmp_path))
    local_embed.reset_embedder()

    v = emb_mod.embed_one("随便一句")
    assert len(v) == emb_mod.FAKE_DIM  # 退到伪向量，但**上传没被打断**
    local_embed.reset_embedder()


def test_维度与列声明不符时判失败(monkeypatch, tmp_path):
    """本地模型换成 768 维（如 bge-base）而列仍是 `VECTOR(1024)` → 必须失败。

    SQLite 是 `LargeBinary`、没有维度概念，所以这条闸**只能在入库前**拦，
    不能指望数据库报错（dev 上会静默通过）。
    """
    e = _stub_embedder(tmp_path, monkeypatch)          # 假模型输出 3 维
    monkeypatch.setattr(emb_mod.settings, "embedding_mode", "local")
    monkeypatch.setattr(local_embed, "get_embedder", lambda: e)
    monkeypatch.setattr(emb_mod, "declared_dim", lambda: 1024)

    vec, source, reason = emb_mod.strict_embed("x")
    assert vec is None and source == emb_mod.EMBED_FAILED
    assert "1024" in reason


def test_非_local_模式不加载模型(monkeypatch):
    """`EMBEDDING_MODE` 不是 local 时必须返回 None —— 否则测试套件会被 330MB 权重拖住。"""
    monkeypatch.setattr(emb_mod.settings, "embedding_mode", "fake")
    local_embed.reset_embedder()
    assert local_embed.get_embedder() is None


# ---------------- 接缝：来源值必须被落库逻辑接住 ----------------

def test_local_向量落库为_ok(monkeypatch):
    """`kb_corpus` 曾只认 `EMBED_REAL` / `EMBED_FAKE` 两种来源 ——

    新增 `local` 后若不改，**整批官方语料会静默变成 `failed`**（一片向量都没有），
    而检索只是"退化到关键词"，看不出是哪里错。这条钉住这个接缝。
    """
    monkeypatch.setattr(
        kb_corpus, "strict_embed", lambda _c: ([0.5] * 1024, emb_mod.EMBED_LOCAL, "")
    )
    stored, status, reason = kb_corpus._corpus_chunk_fields("正文", embed=True, is_pg=False)
    assert status == "ok" and reason == "" and stored


def test_伪向量仍如实记为_fake(monkeypatch):
    """`fake` 不能因为"多了一种真向量来源"而顺手被算成 `ok`（那正是台账的意义）。"""
    monkeypatch.setattr(
        kb_corpus, "strict_embed", lambda _c: ([0.0] * emb_mod.FAKE_DIM, emb_mod.EMBED_FAKE, "")
    )
    _stored, status, _reason = kb_corpus._corpus_chunk_fields("正文", embed=True, is_pg=False)
    assert status == "fake"


# ---------------- 下载器：站点顺序与续传 ----------------

def test_hf_endpoint_环境变量优先(monkeypatch):
    """`HF_ENDPOINT` 是 HuggingFace 官方约定的镜像开关，用户显式指定的必须排第一。"""
    from app.services import onnx_fetch

    monkeypatch.setenv("HF_ENDPOINT", "https://my-mirror.example/")
    sources = onnx_fetch.resolve_sources()
    assert sources[0][0] == "HF_ENDPOINT"
    assert sources[0][1].startswith("https://my-mirror.example/")
    assert any(name == "modelscope" for name, _ in sources)  # 内置源仍在（兜底）


def test_默认源按实测吞吐排序(monkeypatch):
    """**顺序由实测决定**：modelscope 5223 KB/s、hf-mirror 21 KB/s（同一天同一文件）。

    把 hf-mirror 排前面不是"能不能下"的问题，而是"311MB 要 1 分钟还是 9 小时"的问题。
    """
    from app.services import onnx_fetch

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    names = [name for name, _ in onnx_fetch.resolve_sources()]
    assert names[0] == "modelscope"
    assert names.index("hf-mirror") < names.index("huggingface")


def test_服务器忽略_range_时从头写而不是追加(tmp_path, monkeypatch):
    """请求了 `Range` 却收到 200（整份体）→ 必须**从头写**。

    追加会得到「半截旧数据 + 整份新数据」的损坏文件 —— 而它的**体积看着是对的**，
    于是模型加载时才炸（或更糟：加载成功但向量是错的）。这类错很难归因。
    """
    from app.services import onnx_fetch

    tmp = tmp_path / "f.part"
    tmp.write_bytes(b"A" * 100)  # 假装上次下了一半

    class _Resp:
        status = 200

        def __init__(self) -> None:
            self._sent = False

        def read(self, _n):
            if self._sent:
                return b""
            self._sent = True
            return b"B" * 50

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(onnx_fetch.urllib.request, "urlopen", lambda *a, **k: _Resp())
    onnx_fetch._download("http://example/f", tmp)
    assert tmp.read_bytes() == b"B" * 50  # 不是 A*100 + B*50


def test_支持_range_时续传(tmp_path, monkeypatch):
    """311MB 下到一半断了不该从头来 —— 收到 206 就接着写。"""
    from app.services import onnx_fetch

    tmp = tmp_path / "f.part"
    tmp.write_bytes(b"A" * 100)

    class _Resp:
        status = 206

        def __init__(self) -> None:
            self._sent = False

        def read(self, _n):
            if self._sent:
                return b""
            self._sent = True
            return b"B" * 50

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(onnx_fetch.urllib.request, "urlopen", lambda *a, **k: _Resp())
    onnx_fetch._download("http://example/f", tmp)
    assert tmp.read_bytes() == b"A" * 100 + b"B" * 50
