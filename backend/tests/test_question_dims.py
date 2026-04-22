"""P1：题库多维分类（科目 / 学段 / 知识点树 / 难度 / 来源）与三层判重。

这些用例的关键不是「函数能跑」，而是几条**升级期安全性质**：

1. 知识点树可重复补齐、父子关系不丢（它是所有维度统计的锚点）；
2. `subject` / `stage` 为 NULL 的存量题**必须仍在卷面里**——
   否则升级当晚模考直接空卷，且不会有任何报错；
3. 骨架外的考点名留空而不是猜一个挂上去；
4. 判重按考点分桶，不跨考点误杀（相邻考点名天生长得像）。
"""

import json

from app.models import (
    KnowledgePoint,
    Module,
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
    Stage,
    Subject,
    SOURCE_KIND_AI_VARIANT,
    SOURCE_KIND_SEED_TEMPLATE,
)
from app.pipeline.batch_generate import batch_generate
from app.routers.sessions import _official_pool_filters
from app.seed.import_questions import build_questions, import_questions
from app.seed.knowledge_tree import (
    KP_LEVEL,
    MODULE_LEVEL,
    backfill_question_dims,
    ensure_knowledge_tree,
    module_value_of,
    tree_nodes,
)
from app.services.dedup import (
    LEVEL_EXACT,
    LEVEL_NEAR,
    LEVEL_SEMANTIC,
    find_duplicate,
    load_official_index,
)
from app.services.llm_client import GenerationResult, LLMClient


def _item(module: str, kp: str, stem: str, **extra) -> dict:
    payload = {
        "module": module,
        "knowledge_point": kp,
        "stem": stem,
        "options": json.dumps([{"key": "A", "text": "对"}, {"key": "B", "text": "错"}]),
        "answer": json.dumps(["A"]),
        "explanation": "解析",
        "type": QuestionType.SINGLE,
        "source": QuestionSource.POOL,
        "proofread_status": ProofreadStatus.PASSED,
        "aigc_flag": True,
        "version": 1,
    }
    payload.update(extra)
    return payload


def _reset_kps(db, pairs: list[tuple[str, str]]) -> None:
    """清掉指定考点的题目后各重建 1 道，使断言不依赖其他用例遗留的数据。"""
    for _, kp in pairs:
        db.query(Question).filter(Question.knowledge_point == kp).delete(synchronize_session=False)
    db.commit()
    import_questions(db, items=[_item(m, kp, f"{kp}·P1 测试题") for m, kp in pairs])


class _DeterministicClient(LLMClient):
    """同一考点每次返回**同一题干**，用来验证判重跨轮生效。

    刻意声明 `produces_varied_stems = True`（默认值）：近似判重必须处于开启状态，
    否则这里只测到精确层，看不出跨轮判重的真实行为。
    """

    def generate(self, req):
        stem = f"{req.knowledge_point}·P1 固定题干"
        return GenerationResult(
            text=stem,
            payload={
                "module": (req.context or {}).get("module", "职业理念"),
                "knowledge_point": req.knowledge_point,
                "stem": stem,
                "options": [{"key": "A", "text": "对"}, {"key": "B", "text": "错"}],
                "answer": ["A"],
                "explanation": "解析",
                "type": "single",
                "difficulty": "hard",
            },
        )


# ---------------- 知识点树 ----------------


def test_tree_nodes_parent_before_child():
    """父节点必须排在子节点之前——ensure_knowledge_tree 依赖这个次序解析 parent_id。"""
    nodes = tree_nodes()
    assert len(nodes) == 155  # 5 模块 + 150 知识点
    assert sum(1 for n in nodes if n["level"] == MODULE_LEVEL) == 5
    assert sum(1 for n in nodes if n["level"] == KP_LEVEL) == 150

    seen: set[str] = set()
    for node in nodes:
        if node["parent_code"]:
            assert node["parent_code"] in seen, f"{node['code']} 的父节点尚未出现"
        seen.add(node["code"])


def test_ensure_knowledge_tree_is_idempotent(db_session):
    """fixture（init_db）已幂等补齐；重复调用不得新增。"""
    assert db_session.query(KnowledgePoint).count() == 155
    assert ensure_knowledge_tree(db_session) == 0


def test_ensure_knowledge_tree_restores_deleted_node(db_session):
    """删掉一个知识点后重跑：补回来，且父子关系接得上（不能成孤儿）。"""
    code = "综合素质/职业理念/教育观"
    db_session.query(KnowledgePoint).filter(KnowledgePoint.code == code).delete()
    db_session.commit()

    assert ensure_knowledge_tree(db_session) == 1

    node = db_session.query(KnowledgePoint).filter(KnowledgePoint.code == code).one()
    root = db_session.query(KnowledgePoint).filter(KnowledgePoint.code == "综合素质/职业理念").one()
    assert node.level == KP_LEVEL
    assert node.parent_id == root.id
    assert node.subject == Subject.COMPREHENSIVE
    assert node.stage is None  # 三学段通用


def test_module_value_of_normalizes_all_input_styles():
    """调用方传参风格不统一（枚举成员 / 成员名 / 中文值），归一必须都认。"""
    assert module_value_of(Module.PROFESSIONAL_IDEA) == "职业理念"
    assert module_value_of("职业理念") == "职业理念"
    assert module_value_of("PROFESSIONAL_IDEA") == "职业理念"
    assert module_value_of("不存在的模块") is None
    assert module_value_of(None) is None


# ---------------- 维度填充 ----------------


def test_build_questions_marks_seed_template():
    """占位模板题必须自带来源口径——它与 AI 变式的质量差异是数量级的。"""
    items = build_questions()
    assert items
    assert all(it["source_kind"] == SOURCE_KIND_SEED_TEMPLATE for it in items)


def test_import_questions_fills_dims_from_tree(db_session):
    kp = "教育观"
    _reset_kps(db_session, [(Module.PROFESSIONAL_IDEA.value, kp)])

    node = (
        db_session.query(KnowledgePoint)
        .filter(KnowledgePoint.code == f"综合素质/职业理念/{kp}")
        .one()
    )
    q = db_session.query(Question).filter(Question.knowledge_point == kp).one()
    assert q.subject == Subject.COMPREHENSIVE
    assert q.stage is None  # 三学段通用
    assert q.kp_id == node.id
    # 调用方未声明来源 → 留空，不由导入器猜一个默认值
    assert q.source_kind is None


def test_import_questions_preserves_explicit_source_kind(db_session):
    kp = "学生观"
    stem = f"{kp}·显式来源口径"
    db_session.query(Question).filter(Question.stem == stem).delete(synchronize_session=False)
    db_session.commit()

    import_questions(
        db_session,
        items=[_item(Module.PROFESSIONAL_IDEA.value, kp, stem, source_kind=SOURCE_KIND_AI_VARIANT)],
    )

    q = db_session.query(Question).filter(Question.stem == stem).one()
    assert q.source_kind == SOURCE_KIND_AI_VARIANT


def test_import_questions_leaves_unknown_kp_empty(db_session):
    """骨架外的考点名留空，不猜、不强行挂到某个知识点上。"""
    kp = "学生观·骨架外考点"
    db_session.query(Question).filter(Question.knowledge_point == kp).delete(synchronize_session=False)
    db_session.commit()

    import_questions(db_session, items=[_item(Module.PROFESSIONAL_IDEA.value, kp, f"{kp}·题")])

    q = db_session.query(Question).filter(Question.knowledge_point == kp).one()
    assert q.kp_id is None
    assert q.subject == Subject.COMPREHENSIVE  # 模块是官方的，科目仍填得出来


def test_backfill_fills_existing_rows(db_session):
    """模拟 P1 之前的存量数据（维度全空）→ 回填后应有科目与知识点归属。"""
    kp = "教育观"
    _reset_kps(db_session, [(Module.PROFESSIONAL_IDEA.value, kp)])
    db_session.query(Question).filter(Question.knowledge_point == kp).update(
        {"subject": None, "stage": None, "kp_id": None}, synchronize_session=False
    )
    db_session.commit()

    stats = backfill_question_dims(db_session)
    assert stats["updated"] >= 1

    node = (
        db_session.query(KnowledgePoint)
        .filter(KnowledgePoint.code == f"综合素质/职业理念/{kp}")
        .one()
    )
    q = db_session.query(Question).filter(Question.knowledge_point == kp).one()
    assert q.subject == Subject.COMPREHENSIVE
    assert q.kp_id == node.id


# ---------------- 抽题过滤的 NULL 语义（升级期安全性质） ----------------


def test_pool_filter_tolerates_null_dims(db_session):
    """subject / stage 为 NULL 的存量题必须仍能进卷面。

    若写成严格等值过滤，P1 之前入库的题（这两列都是 NULL）会在升级当晚
    从卷面整体消失——用户看到的是「模考没题了」，而不会有任何报错。
    """
    db_session.query(Question).update({"subject": None, "stage": None}, synchronize_session=False)
    db_session.commit()
    import_questions(db_session)  # 保证池里一定有题

    matched = (
        db_session.query(Question)
        .filter(*_official_pool_filters(Subject.COMPREHENSIVE, Stage.PRIMARY))
        .count()
    )
    assert matched > 0


def test_pool_filter_excludes_rejected_and_other_subject(db_session):
    """过滤不能宽到把「驳回题」和「其他科目」也放进来。"""
    kp = "教育观"
    _reset_kps(db_session, [(Module.PROFESSIONAL_IDEA.value, kp)])

    filters = _official_pool_filters(Subject.COMPREHENSIVE, None)
    total_before = db_session.query(Question).filter(*filters).count()

    q = db_session.query(Question).filter(Question.knowledge_point == kp).one()
    q.proofread_status = ProofreadStatus.REJECTED
    db_session.commit()
    assert db_session.query(Question).filter(*filters).count() == total_before - 1

    q.proofread_status = ProofreadStatus.PASSED
    q.subject = Subject.EDU_KNOWLEDGE  # 科目二 → 不该出现在科目一卷面
    db_session.commit()
    assert db_session.query(Question).filter(*filters).count() == total_before - 1


# ---------------- 三层判重 ----------------


def test_dedup_exact_layer_ignores_whitespace_and_fullwidth():
    """精确层要穿过「空白 + 全角/半角」的微差——裸字符串比对拦不住这类。"""
    hit, qid = find_duplicate(
        "下列关于教育观 的表述, 正确的是？",
        existing=[(7, "下列关于教育观的表述，正确的是?")],
    )
    assert hit == LEVEL_EXACT
    assert qid == 7


def test_dedup_exact_layer_has_priority_over_near():
    """同时满足精确与近似时，应报精确——层级信息用于分辨问题性质。"""
    hit, _ = find_duplicate("教育观的正确表述是什么", existing=[(5, "教育观的正确表述是什么")])
    assert hit == LEVEL_EXACT


def test_dedup_near_layer_respects_threshold():
    """近似层由阈值控制：低阈值拦、高阈值放。默认阈值见 config 注释。"""
    a = "德育原则中的疏导原则要求教师循循善诱地启发学生，下列做法体现该原则的是"
    b = "德育原则中的疏导原则要求教师因势利导地引导学生，下列做法体现该原则的是"
    assert find_duplicate(a, existing=[(9, b)], threshold=0.5)[0] == LEVEL_NEAR
    assert find_duplicate(a, existing=[(9, b)], threshold=0.99)[0] is None


def test_dedup_default_threshold_lets_normal_variants_through():
    """官方池默认阈值必须放行「同考点的正常变式」。

    官方变式题是「同考点出多道」，模型天然倾向同一模板（题干高度相似是**正常现象**）。
    若照抄文档侧的 0.6，正常变式会被成批误杀——这正是官方池用 0.85 的原因。
    """
    stem = "下列关于教育观的表述，正确的是？"
    assert find_duplicate(stem, existing=[(3, "下列关于教育观的表述，错误的是？")])[0] is None


def test_dedup_semantic_layer_uses_vectors():
    """语义层：字面完全不同、但向量高度相似（= 同考点换个故事又出一遍）。"""
    hit, qid = find_duplicate(
        "张老师在班会上引导学生讨论班级公约的制定流程",
        existing=[(11, "李老师组织学生共同商定课堂纪律的具体条款")],
        vec=[1.0, 0.0, 0.0],
        existing_vecs=[(11, [0.999, 0.01, 0.0])],
    )
    assert hit == LEVEL_SEMANTIC
    assert qid == 11


def test_dedup_semantic_layer_skipped_without_vectors():
    """没有向量时只是「不启用」语义层，不能退化成误判。"""
    hit, _ = find_duplicate(
        "张老师在班会上引导学生讨论班级公约的制定流程",
        existing=[(11, "李老师组织学生共同商定课堂纪律的具体条款")],
    )
    assert hit is None


def test_load_official_index_separates_buckets_by_kp(db_session):
    """判重按考点分桶，相邻考点名（受教育权 / 受教育权保护）不互相判重。

    这不是「好看」：若把两类考点并成一个全局池，这两个考点的题干相似度
    足以被判成重复（见下方第一个断言），同考点的正常变体就会被成批误杀。
    """
    pairs = [
        ("教育法律法规", "受教育权", "下列关于《受教育权》的表述中，正确的一项是？"),
        ("教育法律法规", "受教育权保护", "下列关于《受教育权保护》的表述中，正确的一项是？"),
    ]
    for _, kp, _stem in pairs:
        db_session.query(Question).filter(Question.knowledge_point == kp).delete(
            synchronize_session=False
        )
    db_session.commit()
    import_questions(db_session, items=[_item(m, kp, stem) for m, kp, stem in pairs])

    index = load_official_index(db_session)
    left = index[("教育法律法规", "受教育权")]
    right = index[("教育法律法规", "受教育权保护")]
    assert len(left) == 1
    assert len(right) == 1

    # 跨考点比对：从 0.5 起就会被判成重复 —— 这正是分桶要避免的系统性误杀
    assert find_duplicate(left[0][1], existing=[right[0]], threshold=0.5)[0] == LEVEL_NEAR
    # 各查各的桶（桶里装的是**同考点的另一道题**，不是相邻考点的）：不误判
    other = "根据《教师法》的规定，教师享有的权利不包括下列哪一项"
    assert find_duplicate(other, existing=left)[0] is None


# ---------------- 官方池批量生成接入维度与判重 ----------------


def test_batch_generate_blocks_repeat_across_runs(db_session):
    """连续两轮生成：第二轮应全部判重命中（而不是又灌一遍同样的题）。"""
    client = _DeterministicClient()

    first = batch_generate(db_session, per_kp=1, client=client)
    assert first["generated"] == 150  # 5 模块 × 30 考点
    assert first["deduped"] == 0

    second = batch_generate(db_session, per_kp=1, client=client)
    assert second["generated"] == 0
    assert second["deduped"] == 150  # 与上一轮逐题重复
    assert second["rejected"] == 0


def test_batch_generate_sets_all_dims(db_session):
    """产物必须带上五轴维度，否则万级题库也只是堆在一个格子里。"""
    batch_generate(db_session, per_kp=1, client=_DeterministicClient())

    q = db_session.query(Question).filter(Question.stem == "教育观·P1 固定题干").one()
    node = (
        db_session.query(KnowledgePoint)
        .filter(KnowledgePoint.code == "综合素质/职业理念/教育观")
        .one()
    )
    assert q.subject == Subject.COMPREHENSIVE
    assert q.stage is None
    assert q.difficulty == "hard"
    assert q.source_kind == SOURCE_KIND_AI_VARIANT
    assert q.kp_id == node.id


def test_batch_generate_leaves_illegal_difficulty_empty(db_session):
    """非法难度留空，不硬塞默认值——塞默认值会让「数据缺失」看起来像统计结论。"""
    from app.pipeline import batch_generate as bg

    class _WeirdDifficulty(_DeterministicClient):
        """题干另起一个（否则会与前一个用例的产物判重，根本进不了库）。"""

        def generate(self, req):
            res = super().generate(req)
            res.payload["stem"] = f"{req.knowledge_point}·P1 非法难度"
            res.payload["difficulty"] = "非常难"
            return res

    batch_generate(db_session, per_kp=1, client=_WeirdDifficulty())
    q = db_session.query(Question).filter(Question.stem == "学生观·P1 非法难度").one()
    assert q.difficulty is None  # 非法值 → 留空
    assert q.kp_id is not None  # 难度缺失不影响其他维度照常落库

    assert bg._difficulty_of({"difficulty": "HARD"}) == "hard"
    assert bg._difficulty_of({"difficulty": "非常难"}) is None
    assert bg._difficulty_of({}) is None
