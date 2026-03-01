"""
CodeSense Backend — Core Configuration
Handles environment, logging, and shared settings.
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
import structlog
import logging


class Settings(BaseSettings):
    # App
    app_name: str = "CodeSense API"
    app_version: str = "1.0.0"
    debug: bool = Field(default=False, env="DEBUG")
    host: str = Field(default="0.0.0.0", env="HOST")
    port: int = Field(default=8000, env="PORT")

    # CORS
    allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        env="ALLOWED_ORIGINS",
    )

    # LLM — Mistral-7B via Ollama (local) or HuggingFace fallback
    llm_backend: str = Field(default="ollama", env="LLM_BACKEND")  # "ollama" | "hf" | "mock"
    ollama_base_url: str = Field(default="http://localhost:11434", env="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="mistral:7b-instruct", env="OLLAMA_MODEL")
    hf_model_id: str = Field(default="mistralai/Mistral-7B-Instruct-v0.2", env="HF_MODEL_ID")
    hf_api_token: str = Field(default="", env="HF_API_TOKEN")
    llm_timeout: float = Field(default=30.0, env="LLM_TIMEOUT")
    llm_max_tokens: int = Field(default=512, env="LLM_MAX_TOKENS")
    llm_temperature: float = Field(default=0.4, env="LLM_TEMPERATURE")

    # Execution sandbox
    execution_timeout_seconds: int = Field(default=5, env="EXECUTION_TIMEOUT")
    execution_memory_limit_mb: int = Field(default=64, env="EXECUTION_MEMORY_MB")
    max_output_chars: int = Field(default=4096, env="MAX_OUTPUT_CHARS")

    # Rate limiting
    rate_limit_requests_per_minute: int = Field(default=60, env="RATE_LIMIT_RPM")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",          # ignore unrecognised env vars / .env keys
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()


def setup_logging(debug: bool = False) -> structlog.BoundLogger:
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger("codesense")


logger = setup_logging(debug=get_settings().debug)
