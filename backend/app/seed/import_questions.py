"""种子题目导入脚本（票 04 / D2 决策）。

- 以大纲考点骨架按模块生成变式题，来源=pool、审校=已抽检通过。
- 幂等：以 (module, knowledge_point, stem) 去重，重跑不重复入库。
- 运行：python -m app.seed.import_questions

⚠️ **本模块 `build_questions()` 产出的是「占位模板题」，不是真实题库**：
题干固定为 `下列关于《{考点}》的表述，正确的是？（种子变式N）`，选项为
「正确表述 / 常见误解 / 无关表述 / 颠倒表述」，**不含任何真实学科内容**。
它的用途只有两个：
  1. 早期 MVP 冷启动的最初池骨架（现已被 `real_questions.json` 的 418 题取代）；
  2. **测试夹具**（`tests/` 直接调用 `import_questions` / `build_questions`）。

因为产物以 `proofread_status=PASSED` 入库，会被抽题直接命中并展示给用户，
**所以 `main()`（CLI）默认拒绝执行**，必须显式 `--allow-placeholder` 才放行。
真实题库请用 `python scripts/import_real_bank.py`（在 backend/ 下运行）。

后续改进（见 docs/course/06-架构现状与改造方向.md 的 P1）：
`Question.source_kind` 列落地后，可用 `source_kind="seed_template"` 结构性区分，
届时占位题无需再靠调用方自觉。
"""

import argparse
import json

from sqlalchemy.orm import Session

from ..db import SessionLocal, init_db
from ..models import ProofreadStatus, Question, QuestionSource, QuestionType
from .questions_data import KNOWLEDGE_POINTS


def build_questions() -> list[dict]:
    items: list[dict] = []
    opt_idx = 0
    for module, points in KNOWLEDGE_POINTS.items():
        for kp in points:
            for variant in range(2):  # 每考点 2 题 => 约 300 题
                correct = chr(ord("A") + (opt_idx % 4))
                options = json.dumps(
                    [
                        {"key": "A", "text": f"关于《{kp}》的正确表述（种子变式{variant + 1}）"},
                        {"key": "B", "text": f"关于《{kp}》的常见误解（种子变式{variant + 1}）"},
                        {"key": "C", "text": f"与《{kp}》无关的表述（种子变式{variant + 1}）"},
                        {"key": "D", "text": f"对《{kp}》的颠倒表述（种子变式{variant + 1}）"},
                    ],
                    ensure_ascii=False,
                )
                stem = f"下列关于《{kp}》的表述，正确的是？（种子变式{variant + 1}）"
                answer = json.dumps([correct], ensure_ascii=False)
                explanation = f"本题考查考点《{kp}》。正确选项为 {correct}：「{kp}」的核心要点如上所述。"
                items.append(
                    {
                        "module": module,
                        "knowledge_point": kp,
                        "stem": stem,
                        "options": options,
                        "answer": answer,
                        "explanation": explanation,
                        "type": QuestionType.SINGLE,
                        "source": QuestionSource.POOL,
                        "proofread_status": ProofreadStatus.PASSED,
                        "aigc_flag": True,
                        "version": 1,
                    }
                )
                opt_idx += 1
    return items


def import_questions(db: Session, items: list[dict] | None = None, clear: bool = False) -> int:
    items = items or build_questions()
    if clear:
        db.query(Question).filter(Question.source == QuestionSource.POOL).delete()
    existing = {
        (q.module, q.knowledge_point, q.stem)
        for q in db.query(Question.module, Question.knowledge_point, Question.stem).all()
    }
    added = 0
    for it in items:
        key = (it["module"], it["knowledge_point"], it["stem"])
        if key in existing:
            continue
        db.add(Question(**it))
        added += 1
    db.commit()
    return added


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。返回进程退出码（0=成功，2=被安全闸拒绝）。

    `argv` 显式传入便于测试（None 时取 sys.argv）。
    库函数 `import_questions()` 不加闸：测试夹具与显式导入场景需要它。
    """
    parser = argparse.ArgumentParser(description="导入考点骨架的占位模板题（非真实题库）")
    parser.add_argument(
        "--allow-placeholder",
        action="store_true",
        help="确认导入占位模板题（无真实学科内容，仅供本地演示，勿进生产库）",
    )
    args = parser.parse_args(argv)

    if not args.allow_placeholder:
        print(
            "[seed] 已拒绝执行：本脚本导入的是**占位模板题**——\n"
            "  题干/选项均为占位文本（如「下列关于《教育观》的表述，正确的是？（种子变式1）」），\n"
            "  无真实学科内容，入库后会被抽题命中并直接展示给用户。\n"
            "  真实题库请用：python scripts/import_real_bank.py（在 backend/ 下运行）\n"
            "  仅本地演示可加 --allow-placeholder。"
        )
        return 2

    init_db()
    db = SessionLocal()
    try:
        added = import_questions(db)
        total = db.query(Question).count()
        print(f"seed: added={added} total={total}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
