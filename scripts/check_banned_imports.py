"""CI guard -- fail if any banned in-process import (PyMuPDF / fitz, AGPL) appears
in the source tree (rule L2). Walk src/tests/scripts and inspect import statements.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
