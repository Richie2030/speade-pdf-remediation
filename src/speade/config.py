"""Typed configuration loaded from config.yaml + environment.

Define the config schema (IO / pipeline / validation / audit) and a loader here.
v1 is fully offline, so there are no runtime secrets/tokens at all (SEC1) -- if any
were ever needed they would come from the environment / a git-ignored .env, never
from config.yaml.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class LocalIOConfig(BaseModel):
    """Local folder-based I/O: read from inbox, write to outbox."""

    inbox: Path = Field(default=Path("data/inbox"), description="Source PDF folder")
    outbox: Path = Field(default=Path("data/outbox"), description="Remediated PDF output folder")
    sidecars: Path = Field(
        default=Path("data/sidecars"),
        description="Per-document sidecar JSON folder (app-internal, kept out of the outbox)",
    )


class IOConfig(BaseModel):
    """Local folder document I/O (offline; the only source in v1)."""

    client: str = Field(default="local", description="Document I/O backend (only 'local' in v1)")
    local: LocalIOConfig = Field(default_factory=LocalIOConfig)


class PipelineConfig(BaseModel):
    """Stage pipeline configuration (roles -> implementation names)."""

    stages: dict[str, str] = Field(
        default_factory=dict,
        description="stage_role: implementation_name mapping (swappable by config)",
    )


class VeraPDFConfig(BaseModel):
    """VeraPDF validation settings (the machine trust gate)."""

    profile: str = Field(
        default="ua1", description="PDF/UA compliance profile (e.g., 'ua1' for PDF/UA-1)"
    )
    path: str | None = Field(
        default=None,
        description="Explicit veraPDF CLI path; default None auto-discovers (PATH, then Docker)",
    )


class ValidationConfig(BaseModel):
    """PDF/UA validation configuration."""

    verapdf: VeraPDFConfig = Field(default_factory=VeraPDFConfig)


class AuditConfig(BaseModel):
    """Append-only audit log configuration."""

    log_path: Path = Field(
        default=Path("data/audit/audit.jsonl"),
        description="Path to audit log (JSONL, one entry per run / gate decision)",
    )


class Config(BaseModel):
    """Root configuration object -- the complete app settings."""

    io: IOConfig = Field(default_factory=IOConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)


def load_config(config_path: Path) -> Config:
    """Load and validate the configuration from a YAML file.

    Non-secret config comes from `config_path` (usually config.yaml, committed).
    v1 is fully offline: there are no runtime secrets to resolve (SEC1).

    Args:
        config_path: path to config.yaml (or equivalent).

    Returns:
        A `Config` instance with all defaults filled in.

    Raises:
        FileNotFoundError: if the config file does not exist.
        ValueError: if the config file is invalid (missing required fields,
            wrong types, etc.).
    """
    import yaml

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    try:
        return Config(**data)
    except Exception as exc:
        raise ValueError(f"Invalid configuration in {config_path}: {exc}") from exc
