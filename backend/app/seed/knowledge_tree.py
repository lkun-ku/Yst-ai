"""知识点树种子（P1）：把「扁平考点字符串」升维成可统计的层级结构。

## 为什么要升维

改造前考点只是 `questions.knowledge_point` 上的一个字符串（150 个扁平值）。
这个表示法答不出下面三个问题，而这正是题库最要紧的三个问题：

1. **哪个知识点缺题？** —— 没有层级就无法按模块聚合覆盖度，
   也就无法回答「职业理念下有 30 个知识点，其中 7 个一道题都没有」。
2. **改一个字为什么全断链？** —— 字符串是冗余副本，题目里的考点名与骨架不再匹配时
   不会有任何报错，只会静默失联。
3. **掌握度能不能细到知识点？** —— 现在只到模块粒度（`Mastery.module`），
   知识点粒度必须有稳定的 id 才存得下。

## 层级怎么定

`level` 存**距根深度**（1=顶层模块），不是固定的「1/2/3」：

- 当前（科目一）2 级：`k1_comprehensive / 职业理念 / 教育观`
- P2 引入官方考纲语料后在中间插入「章节」层，知识点自然降为 3 级

用「深度」而非固定语义，插入层级时不必改表、不必改判定逻辑。

## 与 `questions.knowledge_point` 的关系

P1 起 `kp_id` 是**权威归属**，`knowledge_point` 字符串保留作展示与向后兼容。
二者由 `backfill_question_dims()` 与导入脚本保持一致，不做强一致约束
（存量数据与测试夹具都可能有骨架外的考点名，强行约束只会让写入失败）。
"""

from __future__ import annotations

from sqlalchemy import or_

from ..models import (
    KnowledgePoint,
    Module,
    OFFICIAL_MODULES,
    Question,
    QuestionSource,
    Subject,
)
from .questions_data import KNOWLEDGE_POINTS

#: 当前树只覆盖科目一；stage=None 表示**三学段通用**
#: （综合素质三学段考纲大体一致，后续拿到分学段考纲后再细化填充）。
#:
#: 注意是 `K1_COMPREHENSIVE` 而非旧口径的 `COMPREHENSIVE`：`Subject` 已按官方口径
#: 重构为「科目序号 × 学段」的领域包，枚举值即 `domain_packs/<value>/` 的目录名。
#: 节点 `code` 的前缀由 `TREE_SUBJECT.value` 生成，因此会自动变为 `k1_comprehensive/...`
#: （旧库里的 `综合素质/...` 由迁移 `domain_model_fix` 随存量一起清空）。
TREE_SUBJECT = Subject.K1_COMPREHENSIVE
TREE_STAGE = None

#: 顶层模块的 level；知识点 = 顶层 + 1
MODULE_LEVEL = 1
KP_LEVEL = 2  # noqa: E305


def module_value_of(m) -> str | None:
    """把 module（枚举成员 / 成员名 / 中文值）归一为 `Module.value`；识别不了返回 None。

    需要归一的原因：调用方传参风格不统一——`KNOWLEDGE_POINTS` 的键是枚举成员，
    而测试夹具与历史脚本传的是 `"职业理念"` 这样的中文值（SQLAlchemy 两种都收）。
    """
    if isinstance(m, Module):
        return m.value
    s = str(m or "").strip()
    if not s:
        return None
    for member in Module:
        if s in (member.value, member.name):
            return member.value
    return None


def tree_nodes() -> list[dict]:
    """按「父节点在前」的次序生成全部树节点（幂等写入依赖这个次序）。"""
    nodes: list[dict] = []
    for module in OFFICIAL_MODULES:  # 仓库约定：表达「官方模块」必须用此常量
        points = KNOWLEDGE_POINTS.get(module) or []
        module_code = f"{TREE_SUBJECT.value}/{module.value}"
        nodes.append(
            {
                "code": module_code,
                "parent_code": None,
                "level": MODULE_LEVEL,
                "name": module.value,
                "subject": TREE_SUBJECT,
                "stage": TREE_STAGE,
            }
        )
        for kp in points:
            nodes.append(
                {
                    "code": f"{module_code}/{kp}",
                    "parent_code": module_code,
                    "level": KP_LEVEL,
                    "name": kp,
                    "subject": TREE_SUBJECT,
                    "stage": TREE_STAGE,
                }
            )
    return nodes


def ensure_knowledge_tree(db) -> int:
    """幂等补齐知识点树，返回新增节点数。

    已齐备时只做**一次 SELECT**（读回全部 code）、不产生任何写入，
    因此可以安全地挂在 `db.init_db()` 上——dev/test 每次建表都会调用它。
    """
    nodes = tree_nodes()
    if not nodes:
        return 0

    existing: dict[str, int] = {
        code: kp_id for code, kp_id in db.query(KnowledgePoint.code, KnowledgePoint.id).all()
    }

    added = 0
    for node in nodes:
        if node["code"] in existing:
            continue
        parent_code = node["parent_code"]
        row = KnowledgePoint(
            code=node["code"],
            subject=node["subject"],
            stage=node["stage"],
            parent_id=existing.get(parent_code) if parent_code else None,
            level=node["level"],
            name=node["name"],
        )
        db.add(row)
        db.flush()  # 取回自增主键，供紧随其后的子节点挂 parent_id
        existing[node["code"]] = row.id
        added += 1

    if added:
        db.commit()
    return added


def index_by_module_kp(db) -> dict[tuple[str, str], int]:
    """构建 `{(模块 value, 知识点名): kp_id}` 索引，供批量回填使用。

    批量场景必须用索引而非逐题查询：418 题逐题查 = 418 次往返，
    用索引是固定 2 次查询。
    """
    module_names = {
        kp_id: name
        for kp_id, name in db.query(KnowledgePoint.id, KnowledgePoint.name)
        .filter(KnowledgePoint.level == MODULE_LEVEL)
        .all()
    }
    out: dict[tuple[str, str], int] = {}
    rows = (
        db.query(KnowledgePoint.id, KnowledgePoint.name, KnowledgePoint.parent_id)
        .filter(KnowledgePoint.level == KP_LEVEL)
        .all()
    )
    for kp_id, name, parent_id in rows:
        module_name = module_names.get(parent_id)
        if module_name:
            out[(module_name, name)] = kp_id
    return out


def backfill_question_dims(db) -> dict:
    """回填存量题目的 `subject` / `kp_id`（**不覆盖**已有的非空值，可重复执行）。

    只处理 `source=POOL` 的题：个人题（DOC）不属于官方考纲，本就没有骨架归属。

    `difficulty` 刻意不在此回填——它只存在于 `real_questions.json`，
    回填脚本不该去读数据文件；由导入脚本带入（见 `scripts/import_real_bank.py`）。
    """
    idx = index_by_module_kp(db)
    query = db.query(Question).filter(
        Question.source == QuestionSource.POOL,
        or_(Question.subject.is_(None), Question.kp_id.is_(None)),
    )

    scanned = updated = unresolved = 0
    for item in query.yield_per(200):
        scanned += 1
        module_value = module_value_of(item.module)
        kp_id = idx.get((module_value, item.knowledge_point)) if module_value else None
        if kp_id is None:
            # 骨架外的考点名（历史遗留 / 测试夹具）→ 留空，不猜、不强行挂载
            unresolved += 1
            continue
        if item.subject is None:
            item.subject = TREE_SUBJECT
        if item.kp_id is None:
            item.kp_id = kp_id
        updated += 1

    db.commit()
    return {"scanned": scanned, "updated": updated, "unresolved": unresolved}
