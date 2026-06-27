"""Canvas REST client -- DEFERRED (no API tokens yet); build this LAST.

Same interface as LocalFolderClient (io.base.DocumentClient), so the offline core
swaps to live Canvas by config once tokens land. Re-upload must be sandbox-only,
reversible, and human-confirmed (R3 / SEC4); the token comes from the environment,
never config.
"""

from __future__ import annotations

from pathlib import Path

from speade.io.base import DocRef
