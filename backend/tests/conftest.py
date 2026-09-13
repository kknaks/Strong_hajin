"""Make shared synthetic fixture builders importable from each test layer."""

from pathlib import Path
import sys


TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))
