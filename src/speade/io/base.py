"""The document-source abstraction shared by the local and Canvas clients.

Define the DocRef model and the DocumentClient protocol (list_documents / fetch /
put) here, so swapping LocalFolderClient for a live CanvasClient is a config
edit, not a rewrite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel
