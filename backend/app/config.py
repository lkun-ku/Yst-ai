import os

# 「纸卷 v1」：backend/.env 本地配置加载（setdefault：显式环境变量优先；key 不入 git）
_paper_env_loaded = False
try:
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
    with open(_env_path, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _, _v = _line.partition('=')
                os.environ.setdefault(_k.strip(), _v.strip())
    _paper_env_loaded = True
except OSError:
    pass

from dataclasses import dataclass


@dataclass
class Settings:
    # 生产用 PostgreSQL（如 postgresql+psycopg://user:pwd@host/db，RAG 向量检索依赖 pgvector 扩展）；
    # 本地/测试默认 SQLite，通过环境变量覆盖，DB 引擎可换不影响架构（ADR 未禁止 dev/test 用 SQLite）。
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
    auto_migrate: bool = os.getenv("AUTO_MIGRATE", "true").lower() == "true"
    # 票 13：最小审校后台令牌与内容安全模式。
    # wx=真实微信 msgSecCheck（生产默认）；stub=占位放行。
    # 缺 WX_APPID/WX_SECRET 时 get_content_safety() 自动降级 stub，故默认 wx 安全（#18）。
    admin_token: str = os.getenv("ADMIN_TOKEN", "dev-admin")
    content_safety_mode: str = os.getenv("CONTENT_SAFETY_MODE", "wx")
    wx_appid: str = os.getenv("WX_APPID", "")
    wx_secret: str = os.getenv("WX_SECRET", "")
    # 票 14：实时生成管线（fake=假实现不耗额度；real=OpenAI 兼容接口）
    llm_mode: str = os.getenv("LLM_MODE", "fake")
    llm_api_base: str = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    # #36 备用供应商：主模型失败（限流/不可用）时自动切换，保证聊天链路不被单一供应商波动打断
    llm_fallback_api_base: str = os.getenv("LLM_FALLBACK_API_BASE", "")
    llm_fallback_api_key: str = os.getenv("LLM_FALLBACK_API_KEY", "")
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "")

    # ---------- AI 出题：文档导入与生成（成本硬约束，见 ADR / 方案 2.7） ----------
    # 解析后不留存原文（A3：降低版权风险），仅保留切片
    doc_storage_dir: str = os.getenv("DOC_STORAGE_DIR", "./uploads")
    doc_max_mb: int = int(os.getenv("DOC_MAX_MB", "20"))
    doc_max_chars: int = int(os.getenv("DOC_MAX_CHARS", "300000"))
    # 两级切分：结构切分 + 滑窗
    doc_chunk_size: int = int(os.getenv("DOC_CHUNK_SIZE", "1500"))
    doc_chunk_overlap: int = int(os.getenv("DOC_CHUNK_OVERLAP", "200"))
    # 成本控制六条硬约束
    doc_daily_gen_limit: int = int(os.getenv("DOC_DAILY_GEN_LIMIT", "500"))
    doc_max_q_per_task: int = int(os.getenv("DOC_MAX_Q_PER_TASK", "100"))
    doc_max_input_chars: int = int(os.getenv("DOC_MAX_INPUT_CHARS", "30000"))
    # 生成参数
    # 每批题数：由 6 下调为 3——实测一次生成 6 题时模型常只输出 3 题（提示词含认知层级后更明显），
    # 反而要靠多轮补偿补齐，总耗时更高（133s vs 3 题批约 50s）；小批量成功率更高、总量更可控（#26 A）。
    doc_batch_size: int = int(os.getenv("DOC_BATCH_SIZE", "3"))
    doc_top_k: int = int(os.getenv("DOC_TOP_K", "8"))  # 定点模式召回片段数
    # 自检参数（#26）：自检依据必须与生成量级可比——此前自检只看 800 字而生成可看 3 万字，
    # 依据后段切片出的题会被必然判为「无依据」而误杀，导致大卷 0 产出。
    doc_selfcheck_chars: int = int(os.getenv("DOC_SELFCHECK_CHARS", "8000"))  # 自检依据字符上限
    doc_selfcheck_sample: int = int(os.getenv("DOC_SELFCHECK_SAMPLE", "2"))  # 每批抽检题数（P1）
    # 后台任务并发上限（#26 P2）：出题与向量化分池，避免互相等待导致死锁
    doc_gen_workers: int = int(os.getenv("DOC_GEN_WORKERS", "4"))  # 出题并发
    doc_embed_workers: int = int(os.getenv("DOC_EMBED_WORKERS", "2"))  # 向量化并发
    # 认知层级（#26 A）：默认层级池刻意不含 remember——研究显示 AI 默认约 62% 出「记忆」题
    doc_bloom_levels: str = os.getenv("DOC_BLOOM_LEVELS", "understand,apply,analyze")
    # 题干近似判重阈值（#26 遗留 3）：实测真实语义重复的 Jaccard 约 0.625，不同考点题目通常 <0.4，
    # 故 0.6 能有效拦截且不会误杀。fake 模式整体跳过判重（见 doc_generate._persist_questions）——
    # 同模板伪题相似度约 0.8，不跳过会误杀（实测 12 题只剩 3 题）。
    doc_stem_dup_threshold: float = float(os.getenv("DOC_STEM_DUP_THRESHOLD", "0.6"))

    # ---------- P1：官方题库维度与三层判重 ----------
    # 官方池近似判重阈值。**刻意与文档侧的 0.6 不同**：官方变式题是「同考点出多道」，
    # 模型天然倾向同一模板，题干高度相似是正常现象；用 0.6 会把同考点的正常变式成批误杀
    # （文档侧路线① 已因同类原因把阈值提到 0.85）。此处只拦「几乎字面一致」。
    official_stem_dup_threshold: float = float(os.getenv("OFFICIAL_STEM_DUP_THRESHOLD", "0.85"))
    # 官方池语义判重阈值（题干向量余弦 ≥ 此值即判重）。字面完全不同、但
    # 「考点 + 干扰项同构」的灌水题只能靠语义拦住——这是 L1 精确与 L2 近似都看不见的一类。
    official_stem_semantic_threshold: float = float(
        os.getenv("OFFICIAL_STEM_SEMANTIC_THRESHOLD", "0.92")
    )

    # ---------- 官方语料（考纲 / 法条 / rubric） ----------
    # 目录内的 Markdown 由 kb_corpus.py 切片入库为 `Document(is_official=True)`。
    # 相对路径以 backend/ 为基准；语料随仓库走，不放在 uploads/（后者是用户上传物，被 .gitignore 忽略）。
    official_kb_dir: str = os.getenv("OFFICIAL_KB_DIR", "./data/official")

    # ---------- 引用校验（source_quote 子串硬校验，见 services/citation.py） ----------
    # 开关用于做 A/B 对照（开/关闸门对欠产率与编造率的影响），生产默认开。
    citation_gate_enabled: bool = os.getenv("CITATION_GATE_ENABLED", "true").lower() == "true"
    # 是否要求每道题**必须**给出引用。
    # 默认 false：提示词已要求 source_quote，但模型偶尔漏字段 ——
    # 「没给引用」是提示词遵守度问题，不是编造，拦截它只会让欠产率上升；
    # 「给了引用却定位不到」才是编造，那种情况**无论此开关如何都拦**。
    citation_require_quote: bool = os.getenv("CITATION_REQUIRE_QUOTE", "false").lower() == "true"
    # 归一化后的最短引用长度：低于此值不能作为证据（"的""学生"在任何切片里都能命中），
    # 按编造处理 —— 而被它拦下的真实引用本来就是不合格的引用。
    citation_min_quote_chars: int = int(os.getenv("CITATION_MIN_QUOTE_CHARS", "6"))

    # ---------- 精排（两阶段检索的第二阶段，见 services/rerank.py） ----------
    # impl：onnx（真实模型，生产与评测）/ fake（离线确定性替身，仅供测试）/ off（不重排）。
    # **测试套件默认 off** —— 否则单测要加载 266MB 模型：既慢，又让测试依赖一次下载。
    rerank_impl: str = os.getenv("RERANK_IMPL", "onnx")
    rerank_model_dir: str = os.getenv("RERANK_MODEL_DIR", "./data/models/bge-reranker-base")
    # 精排只作用于召回池的头 `rerank_pool` 条。池子越深、上限越高（上限 = recall@pool），
    # 成本随池深线性上升。20 是折中：法条域稀疏通道 recall@20 = 0.9926。
    rerank_pool: int = int(os.getenv("RERANK_POOL", "20"))
    # 是否把 heading_path 一起喂给精排模型。默认 **True** —— 这是**实测决定的**，不是直觉。
    # 法条域（n=135，编号类查询）：
    #   · 只喂正文：recall@1 = 0.5630、MRR = 0.7181
    #   · 含标题　：recall@1 = 0.8000、MRR = 0.8753
    #   · 完全不精排：recall@1 = 0.7111、MRR = 0.8227
    # **只喂正文比不精排还差**，原因是第 5 项里那个同源的结构事实：
    # 法名与条号只出现在 heading_path 里（正文里没有），
    # 只喂正文时模型无法区分「教师法第七条」与「未成年人保护法第七条」。
    # ⚠️ 但要清楚这笔增益**主要来自元数据**（法名 + 条号）而不是 Cross-Encoder 的语义，
    # 所以 eval 里两种口径**并排报**，不合并成一个数字。
    rerank_include_heading: bool = os.getenv("RERANK_INCLUDE_HEADING", "true").lower() == "true"
    # 送进模型的候选正文截断（字符）。bge-reranker 有效长度有限，过长只增加耗时。
    rerank_max_chars: int = int(os.getenv("RERANK_MAX_CHARS", "512"))

    # ---------- 问答老师 Agent（见 services/teacher_agent.py） ----------
    # 工具循环硬上限。**必须有**：模型可能反复查同一个东西，
    # 而图里的环没有上限就会撞 LangGraph 的 recursion_limit（默认 25）报错。
    # 4 次足够覆盖"检索 → 查条文 → 自检"这类多跳，再多只是在烧调用。
    agent_max_tool_calls: int = int(os.getenv("AGENT_MAX_TOOL_CALLS", "4"))

    # ---------- Embedding（文档级语义检索；fake 模式不耗额度） ----------
    embedding_mode: str = os.getenv("EMBEDDING_MODE", "fake")  # fake / real
    embedding_api_base: str = os.getenv("EMBEDDING_API_BASE", "")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")


settings = Settings()
