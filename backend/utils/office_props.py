"""Clean an Office file we generate before anyone sees it (File > Info in Word, and everything a
viewer doesn't show). Kept as the name its callers import; the cleaning itself is in utils/scrub.py.
"""
from __future__ import annotations

from utils.scrub import clean_office as clean

__all__ = ["clean"]
