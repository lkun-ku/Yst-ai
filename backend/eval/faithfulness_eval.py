"""产出 §6.3 承诺里**最后一格「事实性 faithfulness」**的证据文件。

## 为什么需要单独一个脚本（2026-03-04）

承诺表（`promise_report.py`）里那一格长期写着「（无证据）」，而口径**早就写好了** ——
`metrics.faithfulness_rate(judged, min_factuality=4.0)`：事实性达标的样本占比。
缺的只是**一条产出链**：没人把「教师答案 + 它的检索依据」喂给 judge 并落盘。

## 口径与来源（必须写在证据里）

- **内容** = 问答老师（`grounded` 模式）产出的答案；
- **依据** = 该次回答实际检索到的切片正文（不是引用里被截断的 40 字）；
- **判分** = `eval/judge.py` 的 `factuality`（1–5 分），**达标线 4.0**；
- **来源** = **LLM judge**，不是硬机制 —— 可信度**低于**引用子串校验（后者零误判）。
  这条必须写进产物，否则一个 0.92 会被读成和「编造率 0」同等硬。

## 拒答样本不计入

拒答不是「不忠实」，而是「没内容可判」。把它算作不达标会把"模型老实说不知道"惩罚成
"事实性差" —— 那是两件事（拒答率另有自己的承诺区间 10%~25%）。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime

_HERE = pathlib.Path(__file__).resolve().parent
#: ⚠️ 要插的是 `backend/`（`app` 包在它下面），不是仓库根 —— 算错一级的表现是
#: `ModuleNotFoundError: No module named 'app'`，而它发生在**导入期**，
#: 所以连 `--help` 都跑不起来。
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("LLM_MODE", "fake")

from app.db import SessionLocal, init_db  # noqa: E402
from app.services.kb_corpus import ingest_official_corpus  # noqa: E402
from app.services.kb_retrieval import load_chunks_for_scope  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
from app.services.scope import NAMESPACE_OFFICIAL, Scope  # noqa: E402
from app.services.teacher_agent import ask as teacher_ask  # noqa: E402
from eval import resumable  # noqa: E402
from eval.judge import judge_prompt, parse_judge  # noqa: E402
from eval.metrics import faithfulness_rate  # noqa: E402

_RESULTS = _HERE / "results"
_DB = _HERE / "faithfulness_eval.db"
#: 达标线：judge 的 factuality 是 1–5 整数，4 = 「基本忠于资料」。
MIN_FACTUALITY = 4.0


def _questions(db, limit: int) -> list[str]:
    """从官方语料**程序化**生成可答问题（编号类），与 teacher_eval / retrieval_eval 同思路。

    不写死问题清单：换语料时问题跟着变，否则样本会悄悄与实际语料脱节。
    """
    chunks = load_chunks_for_scope(db, Scope(namespace=NAMESPACE_OFFICIAL))
    out: list[str] = []
    for c in chunks:
        head = str(c.get("heading_path") or "")
        parts = [p.strip() for p in head.split("/") if p.strip()]
        if len(parts) < 2:
            continue
        doc, art = parts[0], parts[-1]
        if not art.startswith("第"):        # 只要「第 N 条」这类，别的够不成可核对的问法
            continue
        q = f"《{doc}》{art}是怎么规定的？"
        if q not in out:
            out.append(q)
        if len(out) >= limit:
            break
    return out


def _answer_and_basis(db, question: str, client) -> dict | None:
    """问一次教师（grounded）→ 取答案正文与它实际检索到的依据。拒答返回 None。"""
    r = teacher_ask(db, 0, question, Scope(namespace=NAMESPACE_OFFICIAL),
                    mode="grounded", client=client)
    if r.get("refused"):
        return None
    ans = r.get("answer")
    text = ans.get("answer") if isinstance(ans, dict) else (ans or "")
    text = str(text or "").strip()
    if not text:
        return None
    basis = " ".join(str(e.get("content") or "") for e in (r.get("evidence") or []))
    return {"question": question, "answer": text, "basis": basis[:1200]}


def _key(rec: dict) -> str:
    return str(rec.get("question") or "")


def _question_key(q: str) -> str:
    """问句字符串的续跑键 = 问句本身 —— 与 `_key` 从记录里取的是**同一个字段**。

    两者必须一致：不一致就会把"已跑过"判成"没跑过"，续跑白花一遍调用（那是本模块
    存在的全部理由）。`tests/test_faithfulness_eval.py` 里"同 tag 重跑应报补 0 条"
    那条测试钉的就是这个一致性。
    """
    return str(q)


def main(limit: int = 12, tag: str = "run") -> dict:
    if _DB.exists():
        _DB.unlink()
    init_db()
    db = SessionLocal()
    ingest_official_corpus(db, embed=False)

    client = get_llm_client()
    mode = "fake" if type(client).__name__ == "FakeLLMClient" else "real"
    questions = _questions(db, limit)
    if not questions:
        raise SystemExit("没能从语料生成可答问题（检查 heading_path 是否含「第N条」）")

    jsonl = resumable.result_path(_RESULTS, "faithfulness", tag)
    done = resumable.load_done(jsonl, key_of=_key)
    #: ⚠️ 待补的是 `pending`，**不是 `questions`** —— 把总题数当待补数打印过：
    #: 实测「已有 12 条」与「本次补 12 条」同框出现（实际补 0 条），
    #: 与 `summarise` 的契约（第二参是**待跑条目**，见 `tests/test_resumable.py`）不符。
    #: 走共用件 `resumable.todo_keys`（"同类只留一个实现"）—— 条目是**问句字符串**，
    #: 由 `_question_key` 说明它的键怎么算（该共用件的实现本就与条目类型无关）。
    pending = resumable.todo_keys(questions, done, key_of=_question_key)
    print(f"问题数 {len(questions)}｜LLM={mode}｜达标线 factuality ≥ {MIN_FACTUALITY}"
          f"｜{resumable.summarise(done, pending)}", flush=True)

    #: ⚠️ **同步点只留一个**：循环里只写 `done`（全量记录的唯一真相），
    #: `judged` / `n_refused` 一律**在循环之后从它派生**。
    #: 踩过的坑（2026-03-04 实测产物为证）：当时循环里只推进 `judged`、不并回 `done`，
    #: 而 `items` 取 `done.values()` → `n_judged=12` 而 `items` 只剩 1 条，产物自己和自己对不上；
    #: 若"本次新算"就是全部（无历史记录），`items` 直接是空的。
    #: 修法不只是"补一处 `done[q] = rec`" —— 那样仍是两份要手工同步的真相，
    #: 下次往循环里再加一条路径（比如换题、加分支）还会漏。
    n_judged_before = sum(1 for r in done.values() if r.get("judged"))
    n_new = 0

    for q in pending:                            # 断点续跑：只跑待补的，已跑的不再花调用
        item = _answer_and_basis(db, q, client)
        if item is None:
            rec = {"question": q, "refused": True, "judged": None}
            resumable.append_record(jsonl, rec)
            done[q] = rec                        # 唯一真相：落盘后立刻并回
            print(f"    [拒答·不计入] {q}", flush=True)
            continue
        ctx = item["basis"]
        score = parse_judge(client.ask(judge_prompt(
            {"stem": item["question"], "options": None, "answer": None,
             "explanation": item["answer"]}, ctx)))
        rec = {"question": q, "answer": item["answer"][:300], "judged": score}
        resumable.append_record(jsonl, rec)
        done[q] = rec                            # 唯一真相：落盘后立刻并回
        n_new += 1
        # 括号里的序号是**累计判分数**（含历史），与改动前一致 —— 便于对照两份运行日志。
        print(f"    [{n_judged_before + n_new}] factuality={score['factuality']:.0f}　{q}",
              flush=True)

    judged = [r["judged"] for r in done.values() if r.get("judged")]   # 派生，不手工同步
    n_refused = sum(1 for r in done.values() if r.get("refused"))      # 派生，不手工同步
    rate = faithfulness_rate(judged, min_factuality=MIN_FACTUALITY)
    result = {
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "llm": f"LLM={mode}（{type(client).__name__}）",
            "channel": "teacher grounded（检索官方语料 → 作答）",
            "judge": "eval/judge.py 的 factuality 维度（1–5 整数）",
            "min_factuality": MIN_FACTUALITY,
            #: 产物要能**自证出自哪次运行**：否则"这 12 条是哪次跑的、取了多少"只能靠人去
            #: 翻 jsonl 的文件名 —— 而承诺表读的是这个文件，不是那个文件名。
            "tag": tag,
            "limit": limit,
            "n_questions": len(questions),
            "n_judged": len(judged),
            "n_refused": n_refused,
            # 来源必须写在产物里：judge 是"模型说的"，比硬机制低一档。
            "source": "LLM judge（**不是硬机制**）—— 可信度低于引用子串校验（后者零误判）",
            "caveat": (
                "样本由语料程序化生成（编号类问法），量小；"
                "它证明的是「链路通 + 当前模型下事实性达标率」，不是普遍事实性。"
                "拒答样本**不计入**：拒答是「没内容可判」，不是「不忠实」（拒答率另有承诺区间）。"
            ),
        },
        "metrics": {"faithfulness_rate": rate, "n": len(judged)},
        # 逐条原始记录（含拒答），**与逐题 jsonl 同源** —— 供人复核那个比例是怎么算出来的。
        "items": list(done.values()),
    }
    _RESULTS.mkdir(parents=True, exist_ok=True)
    # ⚠️ **fake 不许覆盖真证据** —— 与 `promise_report.output_path` / `marking_eval`
    # 同一套处置：fake 只证明"链路通、指标算得出"，它的数字**没有参考价值**；
    # 若写进 `faithfulness.json`，承诺表那一格就会拿一个伪数字去比 0.90。
    out = _RESULTS / ("faithfulness_fake.json" if mode == "fake" else "faithfulness.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n事实性达标率 {rate}（{len(judged)} 条判分，拒答 {n_refused} 条不计入）")
    if mode == "fake":
        print("⚠️ fake 模式：改写为 faithfulness_fake.json（不覆盖真证据）")
    print(f"已写入：{out}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="产出事实性 faithfulness 证据")
    ap.add_argument("--limit", type=int, default=12, help="取样问题数（默认 12）")
    ap.add_argument("--tag", default="run", help="逐题 jsonl 标签；同 tag 重跑只补缺口")
    args = ap.parse_args()
    main(limit=args.limit, tag=args.tag)
