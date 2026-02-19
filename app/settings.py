import os
from pydantic import BaseModel


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


class Settings(BaseModel):
    app_name: str = "SOCLAW"
    jwt_secret: str = _env("SM_JWT_SECRET", "CHANGE_ME_SUPER_SECRET")
    jwt_algorithm: str = "HS256"
    jwt_exp_minutes: int = 60 * 24 * 7  # 7 days session

    database_url: str = _env("SM_DATABASE_URL", "sqlite:///./soclaw.db")

    # AI — set SM_AI_API_KEY env var to enable
    ai_enabled: bool = bool(_env("SM_AI_API_KEY"))
    ai_provider: str = _env("SM_AI_PROVIDER", "groq")
    ai_api_key: str | None = _env("SM_AI_API_KEY")
    ai_model: str = _env("SM_AI_MODEL", "llama-3.1-8b-instant")
    ai_timeout_seconds: int = 12

    retention_days: int = 30


settings = Settings()
