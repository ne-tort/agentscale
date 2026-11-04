from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.domain.errors import AppError

CASES = [
    "..",
    "..%2f..",
    "%2e%2e",
    "%2E%2E%2Fetc",
    "....//etc",
    "a/../../b",
    "a/%2e%2e/b",
    "%25252e%25252e%25252fetc",
    "//etc/passwd",
    "///workspace/x",
    "\\",
    "\\\\server\\share",
    "C:/Windows/win.ini",
    "c:\\windows\\win.ini",
    "docs/./ok.txt",
    "docs//ok.txt",
    " . ",
    "\u2025/secret",
    "a\x00b",
    "%2f%2fetc%2fpasswd",
]
for raw in CASES:
    try:
        out = normalize_workspace_path(raw)
        print(f"ACCEPT {raw!r} -> {out!r}")
    except AppError as exc:
        print(f"REJECT {raw!r} -> {exc.status} {exc.code} {exc.detail!r}")
