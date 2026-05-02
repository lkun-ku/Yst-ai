"""评测指标（`eval/metrics.py`）的用例。

**为什么指标要有单测**：指标是「优化是否有效」的唯一裁判。若裁判本身写错，
后面所有「指标提升」的结论都不可信 —— 而且这种错**不会报错**，只会让数字悄悄偏移。
所以这里用**手算期望值**逐条钉住口径，而不是「跑通就算过」。

同时守住**标注漂移**：黄金片段一旦在语料里找不到，指标会照算但算的是错的。
"""

import json
import os

import pytest

from eval.metrics import (
    RetrievalMetrics,
    citation_hit_rate,
    evaluate_retrieval,
    hit_of,
    hits_of,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

# 三条查询的命中位序列：q1 首位命中、q2 全未命中、q3 第 2 位命中。
_HITS = [[True, False, False], [False, False, False], [False, True, False]]


# ---------------- 黄金判定 ----------------

def test_命中判定是子串而不是近似():
    """与 source_quote 同源：布尔判定、零误判 —— 「关爱学生」不算命中「关爱学生是师德的灵魂」以外的写法。"""
    assert hit_of("关爱学生是师德的灵魂", ["关爱学生是师德的灵魂"]) is True
    assert hit_of("关爱学生是师德的灵魂", ["关爱学生"]) is True
    assert hit_of("关爱学生是师德的灵魂", ["教书育人是教师的天职"]) is False


def test_空黄金或空切片不误判为命中():
    assert hit_of("", ["任意"]) is False
    assert hit_of("任意正文", []) is False
    assert hit_of("任意正文", ["", None]) is False


def test_命中位序列与输入同长同序():
    assert hits_of(["甲", "乙", "丙"], ["乙"]) == [False, True, False]


# ---------------- 三个指标 ----------------

def test_recall_at_k_口径是至少命中一个():
    """q1 榜首命中、q3 第二位命中 → recall@1 = 1/3，recall@2 = 2/3。"""
    assert recall_at_k(_HITS, ks=(1, 2, 3)) == {1: 0.3333, 2: 0.6667, 3: 0.6667}


def test_recall_at_k_空输入返回零而不是除零崩溃():
    assert recall_at_k([], ks=(1, 3)) == {1: 0.0, 3: 0.0}


def test_mrr_未命中记零():
    """1/1 + 0 + 1/2 的平均 = 0.5。"""
    assert mrr(_HITS) == 0.5


def test_ndcg_at_k_手算期望():
    """q1 首位命中 → 1.0；q2 全未命中 → 0；q3 第 2 位命中 → 1/log2(3) ≈ 0.6309。均值 ≈ 0.5436。"""
    assert ndcg_at_k(_HITS, 2) == 0.5436


def test_ndcg_全未命中为零():
    assert ndcg_at_k([[False, False]], 3) == 0.0


def test_引用命中率():
    """3 条引用里 2 条真的出现在切片中。"""
    assert citation_hit_rate(["甲", "乙", "丙"], ["有甲在这里", "乙也在"]) == 0.6667


def test_引用命中率_无引用时为零而不是一():
    """没有引用时不能算「全部命中」—— 那会让「不产出任何引用」变成最优策略。"""
    assert citation_hit_rate([], ["任意"]) == 0.0


# ---------------- 编排入口（retrieve_fn 即消融接口）----------------

def test_evaluate_retrieval_用可替换的检索函数跑通():
    labels = [
        {"query": "甲", "gold": ["甲"]},
        {"query": "乙", "gold": ["乙"]},
    ]

    def perfect(query: str):
        return [query, "其它"]

    metrics = evaluate_retrieval(labels, perfect, ks=(1, 2))
    assert isinstance(metrics, RetrievalMetrics)
    assert metrics.n_queries == 2
    assert metrics.recall_at_k == {1: 1.0, 2: 1.0}
    assert metrics.mrr == 1.0
    assert metrics.ndcg == 1.0


def test_evaluate_retrieval_全错配置得零分():
    labels = [{"query": "甲", "gold": ["甲"]}]
    metrics = evaluate_retrieval(labels, lambda _q: ["完全无关的内容"], ks=(1, 3))
    assert metrics.recall_at_k == {1: 0.0, 3: 0.0}
    assert metrics.mrr == 0.0
    assert metrics.ndcg == 0.0


def test_as_row_列顺序稳定():
    row = RetrievalMetrics(recall_at_k={1: 0.1}, mrr=0.2, ndcg=0.3, n_queries=4, ks=(1,)).as_row()
    assert list(row) == ["n", "recall@1", "mrr", "ndcg"]


# ---------------- 标注漂移（语料一改就会静默失效）----------------

_DATASETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "datasets")


@pytest.mark.parametrize("domain", ["教资", "技术"])
def test_标注集的黄金片段确实存在于语料中(domain):
    """语料被改动 / 换文件时，标注会静默失效 —— 指标照算但算的是错的。

    所以把「黄金片段必须真在语料里」变成一条**可失败的断言**，而不是靠人记得同步。
    标注文件缺失的领域直接跳过（技术域尚未标注）。
    """
    dataset_dir = os.path.join(_DATASETS, domain)
    labels_path = os.path.join(dataset_dir, "retrieval_labels.json")
    if not os.path.exists(labels_path):
        pytest.skip(f"{domain} 域尚无标注集")

    with open(labels_path, encoding="utf-8") as f:
        items = [x for x in json.load(f) if "query" in x]

    corpus = []
    for filename in sorted(os.listdir(dataset_dir)):
        if filename.endswith(".txt"):
            with open(os.path.join(dataset_dir, filename), encoding="utf-8") as f:
                corpus.append(f.read())

    missing = [
        (item["query"], quote)
        for item in items
        for quote in (item.get("gold") or [])
        if not any(quote in text for text in corpus)
    ]
    assert not missing, f"{domain} 域有标注漂移（黄金片段不在语料中）：{missing}"


def test_标注集每条都有查询与黄金片段():
    with open(os.path.join(_DATASETS, "教资", "retrieval_labels.json"), encoding="utf-8") as f:
        items = [x for x in json.load(f) if "query" in x]
    assert len(items) >= 10, "标注集太小，指标分辨率不足"
    assert all(item.get("gold") for item in items), "存在没有黄金片段的查询"


# ---------------- 基准的跨进程可复现性（"数字可信"的前提）----------------

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 在子进程里生成法条标注并打印指纹。**必须用独立进程** ——
#: 被验证的正是「字符串哈希按进程随机化」这件事，同进程内重复调用测不出来。
_CHILD_SCRIPT = "\n".join(
    [
        "import os, sys",
        "sys.path.insert(0, os.getcwd())",
        "from pathlib import Path",
        "from app.services.kb_corpus import plan_file",
        "from eval.retrieval_eval import build_law_labels, labels_fingerprint",
        "files = ['laws/教师法.md', 'laws/未成年人保护法.md', 'laws/义务教育法.md']",
        "chunks = []",
        "for rel in files:",
        "    raw = Path('data/official/' + rel).read_text(encoding='utf-8')",
        "    chunks += [{'content': p.content, 'heading_path': p.heading_path} "
        "for p in plan_file(rel, raw)]",
        "print(labels_fingerprint(build_law_labels(chunks, limit=60)))",
    ]
)


def _fingerprint_with_hash_seed(seed: str) -> str:
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        cwd=_BACKEND,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": seed},
        timeout=180,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


def test_程序化标注跨进程可复现():
    """基准数字的**前提**：同一份语料必须生成同一套标注。

    真实踩坑（ADR-0015 勘误）：主题词排序键写成 `key=lambda t: -df.get(t, 0)`，
    `df` 相同时的先后取决于 `set` 的迭代顺序 → 取决于字符串哈希 →
    CPython 默认**按进程随机化**。于是标注跨进程不同、基准数字每次运行都变，
    而**它不会报错**。当时同一配置两次跑出 recall@1 = 0.6667 与 0.3037。

    **必须用子进程**：同进程内重复调用拿到的是同一套哈希种子，测不出这个问题。
    """
    assert _fingerprint_with_hash_seed("0") == _fingerprint_with_hash_seed("1")
