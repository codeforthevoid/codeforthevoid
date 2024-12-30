import os
from pathlib import Path
from typing import Dict, Any
from pydantic import BaseSettings, PostgresDsn, RedisDsn, validator
import secrets


class Settings(BaseSettings):
    # Base
    PROJECT_NAME: str = "AI Terminal Void"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "production"

    # Security
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALLOWED_HOSTS: list = ["*"]
    CORS_ORIGINS: list = [
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    # Database
    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    SQLALCHEMY_DATABASE_URI: Optional[PostgresDsn] = None

    @validator("SQLALCHEMY_DATABASE_URI", pre=True)
    def assemble_db_connection(cls, v: Optional[str], values: Dict[str, Any]) -> Any:
        if isinstance(v, str):
            return v
        return PostgresDsn.build(
            scheme="postgresql+asyncpg",
            user=values.get("POSTGRES_USER"),
            password=values.get("POSTGRES_PASSWORD"),
            host=values.get("POSTGRES_SERVER"),
            path=f"/{values.get('POSTGRES_DB') or ''}",
        )

    # Redis
    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str
    REDIS_URI: Optional[RedisDsn] = None

    @validator("REDIS_URI", pre=True)
    def assemble_redis_connection(cls, v: Optional[str], values: Dict[str, Any]) -> Any:
        if isinstance(v, str):
            return v
        return RedisDsn.build(
            scheme="redis",
            host=values.get("REDIS_HOST"),
            port=str(values.get("REDIS_PORT")),
            password=values.get("REDIS_PASSWORD"),
        )

    # AI Model
    OPENAI_API_KEY: str
    MODEL_CONFIG: Dict[str, Any] = {
        "model_name": "gpt-3.5-turbo",
        "temperature": 0.7,
        "max_tokens": 150,
        "frequency_penalty": 0.5,
        "presence_penalty": 0.5,
        "request_timeout": 30,
    }

    # WebSocket
    WS_HOST: str = "0.0.0.0"
    WS_PORT: int = 8000
    WS_HEARTBEAT_INTERVAL: int = 30
    WS_CONNECTION_LIFETIME: int = 3600

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60

    # Monitoring
    SENTRY_DSN: Optional[str] = None
    PROMETHEUS_ENABLED: bool = True
    LOGGING_LEVEL: str = "INFO"

    # File Storage
    UPLOAD_DIR: Path = Path("uploads")
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10MB
    ALLOWED_UPLOAD_EXTENSIONS: set = {".txt", ".csv", ".json"}

    # Cache
    CACHE_ENABLED: bool = True
    CACHE_TIMEOUT: int = 300
    CACHE_PREFIX: str = "ai_terminal:"

    # Background Tasks
    CLEANUP_INTERVAL: int = 3600
    MAX_TASK_RETRY: int = 3
    TASK_TIMEOUT: int = 300

    # Performance
    MAX_CONNECTIONS: int = 1000
    CONNECTION_TIMEOUT: int = 30
    POOL_SIZE: int = 20
    MAX_OVERFLOW: int = 10
    POOL_TIMEOUT: int = 30
    POOL_RECYCLE: int = 1800
    POOL_PRE_PING: bool = True

    class Config:
        case_sensitive = True
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()