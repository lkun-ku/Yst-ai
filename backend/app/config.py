import os
from dataclasses import dataclass


@dataclass
class Settings:
    # 生产用 PostgreSQL（如 postgresql+psycopg://user:pwd@host/db，RAG 向量检索依赖 pgvector 扩展）；
    # 本地/测试默认 SQLite，通过环境变量覆盖，DB 引擎可换不影响架构（ADR 未禁止 dev/test 用 SQLite）。
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
    auto_migrate: bool = os.getenv("AUTO_MIGRATE", "true").lower() == "true"
    app_env: str = os.getenv("APP_ENV", "dev")
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

    # ---------- AI 出题：文档导入与生成（成本硬约束，见 ADR / 方案 2.7） ----------
    # 解析后不留存原文（A3：降低版权风险），仅保留切片
    doc_storage_dir: str = os.getenv("DOC_STORAGE_DIR", "./uploads")
    doc_max_mb: int = int(os.getenv("DOC_MAX_MB", "20"))
    doc_max_pages: int = int(os.getenv("DOC_MAX_PAGES", "200"))
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

    # ---------- Embedding（文档级语义检索；fake 模式不耗额度） ----------
    embedding_mode: str = os.getenv("EMBEDDING_MODE", "fake")  # fake / real
    embedding_api_base: str = os.getenv("EMBEDDING_API_BASE", "")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")


settings = Settings()
