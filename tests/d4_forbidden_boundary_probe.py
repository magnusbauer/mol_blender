"""Controlled negative fixture proving the D4 probe guard blocks processes."""
from __future__ import annotations

import subprocess
import sys


subprocess.run([sys.executable, "-c", "pass"], check=True)
