from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_FILES = (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Poem RAG API"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "DB_URL", "database_url"),
    )
    mysql_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("MYSQL_HOST", "DB_HOST", "mysql_host"),
    )
    mysql_port: int = Field(
        default=3306,
        validation_alias=AliasChoices("MYSQL_PORT", "DB_PORT", "mysql_port"),
    )
    mysql_user: str = Field(
        default="root",
        validation_alias=AliasChoices("MYSQL_USER", "DB_USER", "mysql_user"),
    )
    mysql_password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("MYSQL_PASSWORD", "DB_PASSWORD", "mysql_password"),
    )
    mysql_database: str = Field(
        default="poem_rag",
        validation_alias=AliasChoices("MYSQL_DATABASE", "DB_NAME", "mysql_database"),
    )
    sql_echo: bool = False
    auto_create_tables: bool = False

    redis_url: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = Field(default="poem_chunks_v1", min_length=1, max_length=150)
    qdrant_timeout_seconds: float = Field(default=30.0, gt=0)
    qdrant_distance: Literal["Cosine", "Euclid", "Dot"] = "Cosine"

    seed_admin_email: str | None = None
    seed_admin_password: SecretStr | None = None
    seed_admin_display_name: str | None = None

    jwt_secret: SecretStr = SecretStr("dev-only-change-me")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    refresh_cookie_name: str = "poem_refresh_token"

    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = Field(default="deepseek-chat", min_length=1, max_length=150)
    deepseek_timeout_seconds: float = Field(default=60.0, gt=0)
    deepseek_max_output_tokens: int = Field(default=1200, gt=0, le=8192)
    chat_retrieval_limit: int = Field(default=5, ge=1, le=20)
    chat_history_limit: int = Field(default=8, ge=0, le=50)
    dashscope_api_key: SecretStr | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_embedding_model: str = Field(default="text-embedding-v4", min_length=1)
    qwen_embedding_dimension: int | None = Field(default=None, gt=0)
    qwen_embedding_batch_size: int = Field(default=10, gt=0, le=100)
    qwen_embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    qwen_embedding_max_retries: int = Field(default=2, ge=0, le=10)
    qwen_embedding_retry_backoff_seconds: float = Field(default=0.5, ge=0)

    @field_validator("qwen_embedding_dimension", mode="before")
    @classmethod
    def empty_dimension_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def populate_database_url(self) -> Settings:
        if self.database_url:
            return self

        password = quote_plus(self.mysql_password.get_secret_value())
        self.database_url = (
            f"mysql+aiomysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            "?charset=utf8mb4"
        )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
