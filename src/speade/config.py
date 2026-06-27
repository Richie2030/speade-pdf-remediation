"""Typed configuration loaded from config.yaml + environment.

Define the config schema (IO / pipeline / validation / audit) and a loader here.
Non-secret config comes from config.yaml; secrets (tokens) come from the
environment / a git-ignored .env at runtime -- never from config.yaml (rule SEC1).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
