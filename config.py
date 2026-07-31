from typing import Optional

from pydantic import AliasChoices, Field
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

    # Comma-separated list of compose services to watch, e.g. "api,ingestion,worker".
    # DOCKER_SERVICE_NAME is accepted as a legacy alias for single-service setups.
    docker_services: str = Field(
        default="api",
        validation_alias=AliasChoices("docker_services", "docker_service_name"),
    )

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

    @property
    def service_list(self) -> list[str]:
        """DOCKER_SERVICES parsed into an ordered list of service names."""
        return [s.strip() for s in self.docker_services.split(",") if s.strip()]


config = Config()
