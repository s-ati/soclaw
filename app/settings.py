from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "SpeedMatch MVP"
    jwt_secret: str = "CHANGE_ME_SUPER_SECRET"  # change before deploying
    jwt_algorithm: str = "HS256"
    jwt_exp_minutes: int = 60 * 24 * 7  # 7 days session

    database_url: str = "sqlite:///./speedmatch.db"

    # optional AI (stub-ready)
    ai_enabled: bool = False
    ai_provider: str = "none"
    ai_api_key: str | None = None
    ai_timeout_seconds: int = 12

    retention_days: int = 30


settings = Settings()
