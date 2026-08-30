"""In-pod workspace filesystem CLI (k8s exec target)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))


def _resolve(rel: str) -> Path:
    text = (rel or "").strip().replace("\\", "/").lstrip("/")
    root = WORKSPACE_ROOT.resolve()
    target = (root / text).resolve() if text else root
    if target != root and root not in target.parents:
        raise ValueError("path escapes workspace root")
    return target


def _entry_for(path: Path, *, rel: str) -> dict:
    st = path.stat()
    kind = "dir" if path.is_dir() else "file"
    modified = datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat()
    return {
        "name": path.name,
        "path": rel,
        "kind": kind,
        "size": None if kind == "dir" else int(st.st_size),
        "modified_at": modified,
    }


def cmd_list(path: str) -> None:
    target = _resolve(path)
    if not target.exists():
        raise FileNotFoundError(path or ".")
    if not target.is_dir():
        raise NotADirectoryError(path or ".")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        rel = child.relative_to(WORKSPACE_ROOT.resolve()).as_posix()
        entries.append(_entry_for(child, rel=rel))
    print(json.dumps({"entries": entries}), flush=True)


def cmd_stat(path: str) -> None:
    target = _resolve(path)
    if not target.exists():
        raise FileNotFoundError(path)
    rel = target.relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    print(json.dumps(_entry_for(target, rel=rel)), flush=True)


def cmd_read(path: str, *, max_bytes: int) -> None:
    target = _resolve(path)
    if not target.is_file():
        raise IsADirectoryError(path)
    data = target.read_bytes()
    if len(data) > max_bytes:
        data = data[:max_bytes]
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def cmd_rm(path: str) -> None:
    target = _resolve(path)
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    else:
        raise FileNotFoundError(path)


def cmd_mv(src: str, dst: str) -> None:
    s = _resolve(src)
    d = _resolve(dst)
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))


def cmd_cp(src: str, dst: str) -> None:
    s = _resolve(src)
    d = _resolve(dst)
    if s.is_dir():
        if d.exists():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copytree(s, d)
    else:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="workspace_fs")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("path", nargs="?", default="")

    p_stat = sub.add_parser("stat")
    p_stat.add_argument("path")

    p_read = sub.add_parser("read")
    p_read.add_argument("path")
    p_read.add_argument("--max-bytes", type=int, default=10_485_760)

    p_rm = sub.add_parser("rm")
    p_rm.add_argument("path")

    p_mv = sub.add_parser("mv")
    p_mv.add_argument("src")
    p_mv.add_argument("dst")

    p_cp = sub.add_parser("cp")
    p_cp.add_argument("src")
    p_cp.add_argument("dst")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        cmd_list(args.path)
    elif args.cmd == "stat":
        cmd_stat(args.path)
    elif args.cmd == "read":
        cmd_read(args.path, max_bytes=args.max_bytes)
    elif args.cmd == "rm":
        cmd_rm(args.path)
    elif args.cmd == "mv":
        cmd_mv(args.src, args.dst)
    elif args.cmd == "cp":
        cmd_cp(args.src, args.dst)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from exc
