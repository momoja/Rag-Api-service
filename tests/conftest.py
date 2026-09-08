"""Shared test setup: make Lambda handlers importable and configured.

Handler modules under lambda/<function>/ live outside the rag_agent package;
presign's reads DOCUMENTS_BUCKET from the environment at import time
(fail-fast deployment guard). This conftest runs before the test modules,
so it adds the handler directories to sys.path and supplies the env var
first. Patched per-test in the suites themselves.

Both handler modules are importable in one process: presign's is the bare
module name ``handler``, ingest's is ``ingest_handler``.
"""

import os
import sys
from pathlib import Path

_LAMBDA_DIRS = [
    Path(__file__).resolve().parents[1] / "lambda" / "presign_document",
    Path(__file__).resolve().parents[1] / "lambda" / "ingest_document",
]
for _dir in _LAMBDA_DIRS:
    sys.path.insert(0, str(_dir))

os.environ.setdefault("DOCUMENTS_BUCKET", "rag-agent-dev-documents-000000000000")
