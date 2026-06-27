"""Local-folder stand-in for the (token-gated) Canvas client: read source PDFs
from an inbox, write remediated copies to an outbox. Lets the whole offline core
run with no API access. Implements the io.base.DocumentClient interface.
"""

from __future__ import annotations

from pathlib import Path

from speade.io.base import DocRef
