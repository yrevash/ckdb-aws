"""Environment-only configuration suitable for Fargate/Secrets Manager injection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    runtime_mode: str
    host: str
    port: int
    log_level: str
    org_id: UUID
    agent_id: UUID
    aws_region: str
    reasoning_model_id: str
    embedding_model_id: str
    reasoner: str
    cors_origins: tuple[str, ...]
    database_url: str | None
    mcp_url: str | None
    mcp_token: str | None
    recall_backend: str = "mcp"
    cold_start: bool = False
    # Optional per-role DSNs so recall dials postmortem_reader and the act/outcome
    # path dials postmortem_writer (charter R7/T2). When unset, database_url is
    # used for both, but the code still tags each pool with its role so a future
    # cross-wiring fails in-process (see guardrails.roles). Keep both in Secrets
    # Manager in deployed environments.
    reader_database_url: str | None = None
    writer_database_url: str | None = None
    # Shared secret for authenticating the CockroachDB changefeed webhook
    # (HMAC-SHA256 over the raw body). Required to accept webhook ingest.
    changefeed_webhook_secret: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            settings = cls(
                runtime_mode=os.getenv("POSTMORTEM_RUNTIME_MODE", "fake").lower(),
                host=os.getenv("POSTMORTEM_HOST", "0.0.0.0"),
                port=int(
                    os.getenv("POSTMORTEM_PORT")
                    or os.getenv("PORT")
                    or "8080"
                ),
                log_level=os.getenv("POSTMORTEM_LOG_LEVEL", "INFO").upper(),
                org_id=UUID(
                    os.getenv(
                        "POSTMORTEM_ORG_ID",
                        "00000000-0000-0000-0000-000000000001",
                    )
                ),
                agent_id=UUID(
                    os.getenv(
                        "POSTMORTEM_AGENT_ID",
                        "00000000-0000-0000-0000-000000000002",
                    )
                ),
                aws_region=os.getenv("POSTMORTEM_AWS_REGION", "us-east-1"),
                reasoning_model_id=os.getenv(
                    "POSTMORTEM_REASONING_MODEL_ID",
                    "us.anthropic.claude-sonnet-4-6",
                ),
                embedding_model_id=os.getenv(
                    "POSTMORTEM_EMBEDDING_MODEL_ID",
                    "amazon.titan-embed-text-v2:0",
                ),
                reasoner=os.getenv("POSTMORTEM_REASONER", "bedrock").lower(),
                recall_backend=os.getenv(
                    "POSTMORTEM_RECALL_BACKEND", "mcp"
                ).lower(),
                cold_start=os.getenv("POSTMORTEM_COLD_START", "false").lower()
                in {"1", "true", "yes", "on"},
                cors_origins=tuple(
                    origin.strip()
                    for origin in os.getenv(
                        "POSTMORTEM_CORS_ORIGINS",
                        "http://localhost:3000",
                    ).split(",")
                    if origin.strip()
                ),
                database_url=os.getenv("POSTMORTEM_DATABASE_URL") or None,
                mcp_url=os.getenv("POSTMORTEM_MCP_URL") or None,
                mcp_token=os.getenv("POSTMORTEM_MCP_TOKEN") or None,
                reader_database_url=(
                    os.getenv("POSTMORTEM_READER_DATABASE_URL") or None
                ),
                writer_database_url=(
                    os.getenv("POSTMORTEM_WRITER_DATABASE_URL") or None
                ),
                changefeed_webhook_secret=(
                    os.getenv("POSTMORTEM_CHANGEFEED_WEBHOOK_SECRET") or None
                ),
            )
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(f"Invalid Postmortem configuration: {exc}") from exc
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.runtime_mode not in {"fake", "aws"}:
            raise ConfigurationError("POSTMORTEM_RUNTIME_MODE must be fake or aws.")
        if self.reasoner not in {"bedrock", "strands"}:
            raise ConfigurationError("POSTMORTEM_REASONER must be bedrock or strands.")
        if self.recall_backend not in {"mcp", "sql"}:
            raise ConfigurationError(
                "POSTMORTEM_RECALL_BACKEND must be mcp or sql."
            )
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("POSTMORTEM_PORT must be between 1 and 65535.")
        if self.runtime_mode == "aws":
            required = {
                "POSTMORTEM_DATABASE_URL": self.database_url,
            }
            if self.recall_backend == "mcp":
                required.update(
                    {
                        "POSTMORTEM_MCP_URL": self.mcp_url,
                        "POSTMORTEM_MCP_TOKEN": self.mcp_token,
                    }
                )
            missing = [
                name
                for name, value in required.items()
                if not value
            ]
            if missing:
                raise ConfigurationError(
                    "AWS runtime mode requires: " + ", ".join(missing)
                )
