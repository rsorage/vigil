from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM
    llm_provider: str = Field(default="claude", pattern="^(claude|ollama)$")

    # Anthropic
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-4-5")

    # Ollama
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="llama3")

    # Docker
    docker_compose_file: str = Field(default="docker-compose.prod.yml")
    docker_service_name: str = Field(default="api")

    # Path mapping between host and container
    app_source_path: str = Field(default="/home/ubuntu/data-fleet-device-hub")
    app_container_path: str = Field(default="/app")

    # Error lifecycle
    error_inactive_after_hours: int = Field(default=48)

    # GitHub integration (optional)
    github_token: Optional[str] = Field(default=None)
    github_repo: Optional[str] = Field(default=None)  # e.g. "owner/repo"

    # Reporting
    reports_dir: str = Field(default="reports")
    digest_hour: int = Field(default=18)


config = Config()
