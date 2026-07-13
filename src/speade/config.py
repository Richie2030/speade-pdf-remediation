"""Typed configuration loaded from config.yaml + environment.

Define the config schema (IO / pipeline / validation / audit) and a loader here.
Non-secret config comes from config.yaml; secrets (tokens) come from the
environment / a git-ignored .env at runtime -- never from config.yaml (rule SEC1).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class LocalIOConfig(BaseModel):
    """Local folder-based I/O: read from inbox, write to outbox."""

    inbox: str = Field(default="./data/inbox", description="Source PDF folder")
    outbox: str = Field(default="./data/outbox", description="Remediated PDF output folder")


class CanvasIOConfig(BaseModel):
    """Canvas REST API I/O (token-gated; not yet implemented)."""

    base_url: str = Field(description="Canvas instance base URL")
    # token is resolved from env CANVAS_API_TOKEN at runtime, never stored here (SEC1)


class IOConfig(BaseModel):
    """Document source configuration (local folder or Canvas API)."""

    client: str = Field(
        default="local",
        description="I/O adapter: 'local' (offline) or 'canvas' (when tokens land)",
    )
    local: LocalIOConfig = Field(default_factory=LocalIOConfig)
    canvas: CanvasIOConfig | None = None


class PipelineConfig(BaseModel):
    """Stage pipeline configuration (roles -> implementation names)."""

    stages: dict[str, str] = Field(
        default_factory=lambda: {"passthrough": "noop"},
        description="stage_role: implementation_name mapping (swappable by config)",
    )


class VeraPDFConfig(BaseModel):
    """VeraPDF validation settings (the machine trust gate)."""

    profile: str = Field(
        default="ua1", description="PDF/UA compliance profile (e.g., 'ua1' for PDF/UA-1)"
    )


class ValidationConfig(BaseModel):
    """PDF/UA validation configuration."""

    verapdf: VeraPDFConfig = Field(default_factory=VeraPDFConfig)


class AuditConfig(BaseModel):
    """Append-only audit log configuration."""

    log_path: str = Field(
        default="./data/audit/audit.jsonl",
        description="Path to audit log (JSONL format, one entry per run/gate decision)",
    )


class AppConfig(BaseModel):
    """Root configuration object — the complete app settings."""

    io: IOConfig = Field(default_factory=IOConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)


def load_config(config_path: Path) -> AppConfig:
    """Load and validate the configuration from a YAML file.

    Non-secret config comes from `config_path` (usually config.yaml, committed).
    Secrets (Canvas/Ally tokens) are resolved from the environment at runtime
    and never stored in the config file (rule SEC1).

    Args:
        config_path: path to config.yaml (or equivalent).

    Returns:
        An `AppConfig` instance with all defaults filled in.

    Raises:
        FileNotFoundError: if the config file does not exist.
        ValueError: if the config file is invalid (missing required fields,
            wrong types, etc.).
    """
    import yaml

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    try:
        return AppConfig(**data)
    except Exception as exc:
        raise ValueError(f"Invalid configuration in {config_path}: {exc}") from exc
