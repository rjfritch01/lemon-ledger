from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://lemon:changeme@localhost:5432/lemon_ledger"
    redis_url: str = "redis://localhost:6379/0"

    explorer_lemonchain_url: str = "https://explorer.lemonchain.io/api"
    explorer_lemonchain_testnet_url: str = "https://explorer-testnet.lemonchain.io/api"
    explorer_request_timeout_s: float = 15.0
    explorer_rate_limit_rps: float = 4.0
    explorer_rate_limit_burst: int = 4
    explorer_page_size: int = 1000
    explorer_max_retries: int = 5

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
