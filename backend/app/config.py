import os
from dataclasses import dataclass


@dataclass
class Settings:
    # 生产用 MySQL（如 mysql+pymysql://user:pwd@host/db）；
    # 本地/测试默认 SQLite，通过环境变量覆盖，DB 引擎可换不影响架构（ADR 未禁止 dev/test 用 SQLite）。
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
    auto_migrate: bool = os.getenv("AUTO_MIGRATE", "true").lower() == "true"
    app_env: str = os.getenv("APP_ENV", "dev")


settings = Settings()
