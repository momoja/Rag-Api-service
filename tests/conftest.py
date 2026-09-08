"""Shared test setup: make Lambda handlers importable and configured.

Handler modules under lambda/<function>/ live outside the rag_agent package
and read DOCUMENTS_BUCKET from the environment at import time (fail-fast
deployment guard). This conftest runs before the test modules, so it adds
the directory to sys.path and supplies the env var first. Patched per-test
in the suites themselves.
"""

import os
import sys
from pathlib import Path

_LAMBDA_DIR = Path(__file__).resolve().parents[1] / "lambda" / "presign_document"
sys.path.insert(0, str(_LAMBDA_DIR))

os.environ.setdefault("DOCUMENTS_BUCKET", "rag-agent-dev-documents-000000000000")
