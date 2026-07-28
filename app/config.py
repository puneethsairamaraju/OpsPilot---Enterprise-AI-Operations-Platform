from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OpsPilot"
    app_env: str = "development"
    database_url: str = "sqlite:///./opspilot.db"
    jwt_secret: str = "development-only-secret"
    access_token_minutes: int = 480
    approval_confidence_threshold: float = 0.62

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

