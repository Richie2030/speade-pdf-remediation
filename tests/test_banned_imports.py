"""Test that no in-process AGPL imports (PyMuPDF / fitz) exist anywhere --
exercises scripts/check_banned_imports.py.
"""

import subprocess
import sys
from pathlib import Path
