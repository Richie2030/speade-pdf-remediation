"""Typed configuration loaded from `config.yaml` + environment.

Non-secret config lives in the committed `config.yaml`. Secrets (Canvas/Ally
tokens) are loaded from the environment / a git-ignored `.env` at runtime and
NEVER read from `config.yaml` (plan SEC1).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class LocalIO(BaseModel):
    inbox: Path = Path("./data/inbox")
    outbox: Path = Path("./data/outbox")


class CanvasIO(BaseModel):
    base_url: str = "https://canvas.example.edu"


class IOConfig(BaseModel):
    client: str = "local"
    local: LocalIO = Field(default_factory=LocalIO)
    canvas: CanvasIO = Field(default_factory=CanvasIO)


class PipelineConfig(BaseModel):
    stages: dict[str, str] = Field(default_factory=dict)


class VeraPDFConfig(BaseModel):
    profile: str = "ua1"


class ValidationConfig(BaseModel):
    verapdf: VeraPDFConfig = Field(default_factory=VeraPDFConfig)


class AuditConfig(BaseModel):
    log_path: Path = Path("./data/audit/audit.jsonl")


class Config(BaseModel):
    io: IOConfig = Field(default_factory=IOConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)

    @classmethod
    def load(cls, path: Path = Path("config.yaml")) -> Config:
        load_dotenv()  # secrets from .env (git-ignored) -> os.environ
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls.model_validate(data or {})
