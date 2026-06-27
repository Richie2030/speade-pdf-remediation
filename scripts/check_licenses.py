"""CI guard -- dependency-licence allowlist gate (rule L6). Run pip-licenses over
the shipped runtime closure and fail on anything not permissive / weak-copyleft
(MPL); GPL/AGPL/LGPL are subprocess-only, never pip dependencies.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
