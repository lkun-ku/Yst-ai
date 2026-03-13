import os
from dataclasses import dataclass


@dataclass
class Settings:
    # 生产用 MySQL（如 mysql+pymysql://user:pwd@host/db）；
    # 本地/测试默认 SQLite，通过环境变量覆盖，DB 引擎可换不影响架构（ADR 未禁止 dev/test 用 SQLite）。
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
    auto_migrate: bool = os.getenv("AUTO_MIGRATE", "true").lower() == "true"
    app_env: str = os.getenv("APP_ENV", "dev")
    # 票 13：最小审校后台令牌与内容安全模式（stub=占位放行；wx=微信 msgSecCheck）
    admin_token: str = os.getenv("ADMIN_TOKEN", "dev-admin")
    content_safety_mode: str = os.getenv("CONTENT_SAFETY_MODE", "stub")
    wx_appid: str = os.getenv("WX_APPID", "")
    wx_secret: str = os.getenv("WX_SECRET", "")
    # 票 14：实时生成管线（fake=假实现不耗额度；real=OpenAI 兼容接口）
    llm_mode: str = os.getenv("LLM_MODE", "fake")
    llm_api_base: str = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")


settings = Settings()
