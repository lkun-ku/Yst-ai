# -*- coding: utf-8 -*-
"""建/补知识点树，并把存量题目回填到维度上（P1）。在 backend/ 下运行，可重复执行。

用法：
    python scripts/seed_knowledge_tree.py

**为什么需要这个脚本**：知识点树是**参考数据**，不写在 Alembic 迁移里
（迁移只建 schema，不把 150+ 行考纲冻死在迁移文件中）。
`AUTO_MIGRATE=true` 时 `db.init_db()` 会自动补齐；生产若以 `AUTO_MIGRATE=false` 启动，
升级后必须显式跑本脚本，否则 `questions.kp_id` 恒为空、覆盖度统计没有锚点。
"""
import os
import sys

sys.path.insert(0, os.getcwd())  # 在 backend/ 下运行

import app.models  # noqa: F401  确保元数据含全部表
from app.db import Base, SessionLocal, engine
from app.models import KnowledgePoint, Question, Subject
from app.seed.knowledge_tree import backfill_question_dims, ensure_knowledge_tree, tree_nodes


def main() -> int:
    # 刻意不用 init_db()：它内部也会补齐知识点树，那样这里的 added 永远是 0，
    # 运维看不到「本次到底补了多少」。这里只建表，把补齐交给下方显式调用。
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        added = ensure_knowledge_tree(db)
        expected = len(tree_nodes())

        stats = backfill_question_dims(db)

        total_kp = db.query(KnowledgePoint).count()
        with_subject = db.query(Question).filter(Question.subject.isnot(None)).count()
        total_q = db.query(Question).count()

        print(f"知识点树：本次新增 {added} 个节点，现有 {total_kp}/{expected} 个")
        print(
            f"题目回填：扫描 {stats['scanned']} 条，更新 {stats['updated']} 条，"
            f"骨架外考点 {stats['unresolved']} 条（留空，不猜）"
        )
        print(f"维度覆盖：subject 已填 {with_subject}/{total_q} 条")
        if total_kp != expected:
            print(f"⚠️ 节点数不等于预期 {expected}，请检查是否有同名 code 冲突")
            return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
