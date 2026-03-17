"""三路线对比实验 runner：baseline(现有单文档管线) / kb_handwritten(路线②) / kb_langgraph(路线③)。

控制变量：三条路线共享同一 LLM·Embedding 接缝、同一检索、同一提示词与校验逻辑；
唯一差异是编排层。评测测量「出题质量（LLM-as-judge 五维）」与「成本（LLM 调用次数 +
提示词字符数作为 token 代理）」。

运行：
    python backend/eval/run_eval.py            # fake 模式离线（默认）
    python backend/eval/run_eval.py --real     # 需先 export EMBEDDING_MODE=real LLM_MODE=real 及对应 key

说明：fake 模式下 LLM 为确定性伪响应，三条路线产出质量一致（均为合法题），差异仅体现在
成本（路线②/③ 多出检索评分与生成自检的 LLM 调用）。该差异本身即证明「框架不改变单题质量，
只改变结构与成本」；真实 LLM 下质量闭环才会带来质量增益——真实实验需 API key，harness 已支持。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

# 必须在导入 app 之前设定 DATABASE_URL，避免污染 dev.db / 测试库。
# 默认用独立 eval_kb.db 并强制覆盖（而非 setdefault）：若继承外部 DATABASE_URL 会落到
# dev/测试库，与既有 candidate id（如 9101）撞唯一约束。
# 工单 18：需要跑 PG 分支时，用 EVAL_DATABASE_URL 显式指定即可（如腾讯云 PG），
# 此时 retrieve_by_scope 会自动走 retrieve_by_scope_pg（pgvector + HNSW）。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
os.environ["DATABASE_URL"] = os.environ.get("EVAL_DATABASE_URL") or "sqlite:///" + os.path.join(
    _ROOT, "backend", "eval", "eval_kb.db"
)

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Candidate, Document, DocumentChunk, Module  # noqa: E402
from app.services.embedding import embed_one, encode_vector  # noqa: E402
from app.services import kb_generate, kb_graph  # noqa: E402
from app.services.doc_generate import generate_for_document  # noqa: E402
from app.services.kb_retrieval import load_chunks, retrieve_by_scope  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
import app.services.llm_client as _lc  # noqa: E402
import app.services.doc_generate as _dg  # noqa: E402
import app.services.kb_generate as _kg  # noqa: E402
import app.services.kb_graph as _kgraph  # noqa: E402
try:  # 兼容脚本直接运行（__package__ 为空）与 pytest 包上下文
    from .judge import JUDGE_DIMS, avg_scores, judge_questions
except ImportError:  # pragma: no cover
    from eval.judge import JUDGE_DIMS, avg_scores, judge_questions  # noqa: E402

DATASETS_DIR = os.path.join(os.path.dirname(__file__), "datasets")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
SPEC = [{"type": "single", "count": 3}, {"type": "judge", "count": 1}]
DIFFICULTY = "medium"


class CountingWrapper:
    """统计 LLM 调用次数与提示词字符数（token 代理），委托给真实/伪 client。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.prompt_chars = 0

    def ask(self, prompt, timeout=30):
        self.calls += 1
        self.prompt_chars += len(prompt)
        return self.inner.ask(prompt, timeout)

    def generate(self, req):
        self.calls += 1
        self.prompt_chars += len(str(getattr(req, "context", ""))) + len(getattr(req, "knowledge_point", "") or "")
        return self.inner.generate(req)


def _q_to_dict(q) -> dict:
    return {
        "stem": q.stem,
        "options": json.loads(q.options) if q.options else None,
        "answer": json.loads(q.answer) if q.answer else None,
        "explanation": q.explanation,
    }


def _seed_dataset(db, cand_id: int, dataset_dir: str) -> None:
    db.add(Candidate(id=cand_id, unionid=f"eval{cand_id}"))
    doc_idx = cand_id * 10
    for fn in sorted(os.listdir(dataset_dir)):
        if not fn.endswith(".txt"):
            continue
        with open(os.path.join(dataset_dir, fn), "r", encoding="utf-8") as f:
            text = f.read().strip()
        doc_idx += 1
        db.add(
            Document(
                id=doc_idx, candidate_id=cand_id, title=fn,
                file_type="txt", storage_path=fn,
            )
        )
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        # 工单 18：PG 上必须同时写 embedding_vec，否则检索 SQL 的
        # `embedding_vec IS NOT NULL` 会过滤掉全部切片，导致 PG 分支静默返空。
        is_pg = db.get_bind().dialect.name == "postgresql"
        for seq, para in enumerate(paras):
            vec = embed_one(para)
            extra = {"embedding_vec": vec} if is_pg else {}
            db.add(
                DocumentChunk(
                    document_id=doc_idx,
                    seq=seq,
                    content=para,
                    heading_path=fn.replace(".txt", ""),
                    char_count=len(para),
                    embedding=encode_vector(vec),
                    embed_status="ok",
                    **extra,
                )
            )
    db.commit()


def _ctx_for(db, cand_id, scope) -> str:
    chunks = retrieve_by_scope(db, cand_id, scope, k=6)
    return "\n".join(c.get("content", "") for c in chunks)


def run_baseline(db, cand_id, scope) -> list:
    """路线①：现有单文档管线（spot 模式）对各文档尽力而为。"""
    chunks_all = load_chunks(db, cand_id)
    by_doc: dict[int, list] = {}
    for c in chunks_all:
        by_doc.setdefault(c["document_id"], []).append(c)
    created = []
    for doc_id, chunks in by_doc.items():
        doc = db.get(Document, doc_id)
        created += generate_for_document(
            db, doc, chunks, SPEC, mode="spot",
            difficulty=DIFFICULTY, focus=scope, scope=scope,
        )
    return created


def run_route(db, cand_id, scope, route: str, counter: CountingWrapper) -> list:
    if route == "baseline":
        return run_baseline(db, cand_id, scope)
    if route == "kb_handwritten":
        return kb_generate.generate_by_scope(
            db, cand_id, scope, SPEC, difficulty=DIFFICULTY, enable_loop=True
        )
    if route == "kb_langgraph":
        return kb_graph.generate_by_scope_graph(
            db, cand_id, scope, SPEC, difficulty=DIFFICULTY, enable_loop=True
        )
    raise ValueError(route)


ROUTES = ["baseline", "kb_handwritten", "kb_langgraph"]


def main(real: bool = False) -> dict:
    # 评测库为专用库，每次运行重建以保证可重复（不影响 dev/test 库）
    db_file = os.path.join(_ROOT, "backend", "eval", "eval_kb.db")
    if os.path.exists(db_file):
        os.remove(db_file)
    init_db()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    db = SessionLocal()
    counter = CountingWrapper(get_llm_client())
    # 让三条路线都走同一个计数 client（各模块均以 `from .llm_client import get_llm_client`
    # 在导入期绑定了名字，须逐一 patch 其命名空间内的引用）
    _lc.get_llm_client = lambda: counter
    _dg.get_llm_client = lambda: counter
    _kg.get_llm_client = lambda: counter
    _kgraph.get_llm_client = lambda: counter

    per_scope = []
    cand_id = 9100
    for domain in ["教资", "技术"]:
        dataset_dir = os.path.join(DATASETS_DIR, domain)
        with open(os.path.join(dataset_dir, "scopes.json"), "r", encoding="utf-8") as f:
            scopes = json.load(f)
        cand_id += 1
        _seed_dataset(db, cand_id, dataset_dir)
        for sc in scopes:
            scope = sc["scope"]
            ctx = _ctx_for(db, cand_id, scope)
            for route in ROUTES:
                counter.calls = 0
                counter.prompt_chars = 0
                qs = run_route(db, cand_id, scope, route, counter)
                qdicts = [_q_to_dict(q) for q in qs]
                scores = judge_questions(counter.inner, qdicts, ctx) if qdicts else []
                rec = {
                    "domain": domain,
                    "scope": scope,
                    "kind": sc["kind"],
                    "route": route,
                    "n_questions": len(qs),
                    "scores": avg_scores(scores),
                    "llm_calls": counter.calls,
                    "prompt_chars": counter.prompt_chars,
                }
                per_scope.append(rec)
                print(f"[{domain}|{sc['kind']}|{route}] n={len(qs)} calls={counter.calls} scores={rec['scores']}")

    # 汇总：每路线跨域平均
    route_summary = {}
    for route in ROUTES:
        recs = [r for r in per_scope if r["route"] == route]
        avg = {d: round(sum(r["scores"][d] for r in recs) / len(recs), 3) for d in JUDGE_DIMS}
        route_summary[route] = {
            "avg_scores": avg,
            "total_llm_calls": sum(r["llm_calls"] for r in recs),
            "total_prompt_chars": sum(r["prompt_chars"] for r in recs),
            "avg_calls_per_question": round(
                sum(r["llm_calls"] for r in recs) / max(1, sum(r["n_questions"] for r in recs)), 2
            ),
        }

    result = {
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "mode": "real" if real else "fake",
            "llm_mode": os.environ.get("LLM_MODE", "fake"),
            "embedding_mode": os.environ.get("EMBEDDING_MODE", "fake"),
            "spec": SPEC,
            "routes": ROUTES,
        },
        "per_scope": per_scope,
        "route_summary": route_summary,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(RESULTS_DIR, f"eval_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    _write_markdown(os.path.join(RESULTS_DIR, f"eval_{ts}.md"), result)
    print(f"\n结果已写入：{json_path}")
    return result


def _write_markdown(path: str, result: dict) -> None:
    lines = ["# 三路线对比实验报告", "", f"- 模式：{result['metadata']['mode']}（LLM={result['metadata']['llm_mode']}, Embedding={result['metadata']['embedding_mode']}）",
             f"- 时间：{result['metadata']['time']}", ""]
    lines.append("## 路线汇总（跨域平均）")
    lines.append("")
    lines.append("| 路线 | 事实性 | 覆盖 | 唯一性 | 解析 | 难度 | LLM调用 | 提示词字符 | 每题调用 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for route, s in result["route_summary"].items():
        sc = s["avg_scores"]
        lines.append(
            f"| {route} | {sc['factuality']} | {sc['coverage']} | {sc['uniqueness']} | "
            f"{sc['explanation']} | {sc['difficulty']} | {s['total_llm_calls']} | "
            f"{s['total_prompt_chars']} | {s['avg_calls_per_question']} |"
        )
    lines.append("")
    lines.append("## 逐 scope 明细")
    lines.append("")
    lines.append("| 领域 | 查询类型 | 路线 | 题数 | 事实性 | 覆盖 | 唯一性 | 解析 | 难度 | 调用 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in result["per_scope"]:
        sc = r["scores"]
        lines.append(
            f"| {r['domain']} | {r['kind']} | {r['route']} | {r['n_questions']} | "
            f"{sc['factuality']} | {sc['coverage']} | {sc['uniqueness']} | {sc['explanation']} | "
            f"{sc['difficulty']} | {r['llm_calls']} |"
        )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="真实模式（需设置 EMBEDDING_MODE/LLM_MODE=real 及 key）")
    args = ap.parse_args()
    main(real=args.real)
