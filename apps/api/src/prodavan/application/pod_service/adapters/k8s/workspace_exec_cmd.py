"""Build in-pod workspace_fs exec argv (embed source for older sandbox images)."""

from __future__ import annotations

import base64
import importlib
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _workspace_fs_source_b64() -> str:
    mod = importlib.import_module("prodavan.runtime.workspace_fs")
    path = Path(mod.__file__)
    return base64.b64encode(path.read_bytes()).decode("ascii")


def build_workspace_fs_command(args: list[str]) -> list[str]:
    """Return argv for `python -c …` that runs workspace_fs inside the pod."""
    encoded = _workspace_fs_source_b64()
    argv_json = json.dumps(args)
    script = (
        "import base64, json; "
        f"code = base64.b64decode({encoded!r}); "
        "ns = {}; "
        "exec(compile(code, 'workspace_fs.py', 'exec'), ns); "
        f"ns['main'](json.loads({argv_json!r}))"
    )
    return ["python", "-c", script]
