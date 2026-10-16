#!/usr/bin/env python3
"""CI VM disk hygiene: prune CI leftovers and guard the disk.

Runs from a systemd timer on the CI VM. The CI runners share this VM with the
k3s dev cluster; a full disk takes the whole dev contour down with it, so this
is deliberately independent of any workflow (a cancelled or crashed run cannot
clean up after itself).

What it does, every run:
  1. remove anonymous docker volumes left by smoke/nightly postgres containers
     (postgres images declare VOLUME, so `docker rm -f` leaks ~140 MB each);
  2. drop orphaned agentscale-* smoke/pg containers that are not running;
  3. keep only the newest N sha-tagged first-party images (k3s runs from
     containerd, so docker-side sha images are pure CI leftovers);
  4. cap the two persistent buildx caches (if their state volume is oversized);
  5. warn (journal + optional ntfy) when the root filesystem crosses thresholds.

Safety: named volumes and images referenced by RUNNING containers are never
touched; only anonymous (64-hex) volumes and sha-tagged first-party images that
nothing runs are pruned.

Run as root: sudo python3 ci-disk-guard.py [--dry-run] [--warn-pct 80]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime

KEEP_IMAGES_PER_REPO = 2
BUILDX_STATE_CAP_MB = {
    "buildx_buildkit_agentscale-ci0_state": 6 * 1024,
    "buildx_buildkit_prodavan-ci0_state": 4 * 1024,
}
ANON_VOLUME_RE = re.compile(r"^[0-9a-f]{64}$")
CI_CONTAINER_RE = re.compile(
    r"agentscale-(migrate-smoke|e2e-pg|nightly-pg|ci-pg)"
)
FIRST_PARTY_REPO_RE = re.compile(r"^ghcr\.io/[^/]+/agentscale-(api|web)$")


def sh(args: list[str], *, check: bool = False) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def free_pct(path: str = "/") -> float:
    usage = shutil.disk_usage(path)
    return 100.0 * usage.used / usage.total


def running_image_ids() -> set[str]:
    ids: set[str] = set()
    for line in sh(["docker", "ps", "--no-trunc", "--format", "{{.Image}}"]).splitlines():
        line = line.strip()
        if not line:
            continue
        ids.update(sh(["docker", "image", "ls", "--no-trunc", "-q", line]).splitlines())
        ids.add(line)
    return ids


def prune_containers(dry: bool) -> int:
    removed = 0
    out = sh(["docker", "ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"])
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        cid, name, status = parts[0], parts[1], parts[2]
        if not CI_CONTAINER_RE.search(name):
            continue
        if status.lower().startswith("up"):
            continue
        print(f"  container: rm -fv {name} ({status})")
        if not dry:
            sh(["docker", "rm", "-fv", cid])
        removed += 1
    return removed


def prune_volumes(dry: bool) -> int:
    """Anonymous, unreferenced volumes only (never named volumes)."""
    dangling = [
        v for v in sh(["docker", "volume", "ls", "-qf", "dangling=true"]).splitlines()
        if ANON_VOLUME_RE.match(v.strip())
    ]
    if not dangling:
        return 0
    print(f"  volumes: {len(dangling)} anonymous dangling -> prune")
    if not dry:
        sh(["docker", "volume", "prune", "-f"])
    return len(dangling)


def prune_images(dry: bool) -> int:
    """Keep the newest N sha tags per first-party repo; never touch running ones."""
    live = running_image_ids()
    removed = 0
    out = sh([
        "docker", "images", "--no-trunc",
        "--format", "{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}",
    ])
    by_repo: dict[str, list[tuple[str, str, str]]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        repo, tag, image_id, created = parts[0], parts[1], parts[2], parts[3]
        if not FIRST_PARTY_REPO_RE.match(repo):
            continue
        if not re.fullmatch(r"[0-9a-f]{12}", tag):
            continue  # :latest and moving tags are kept
        by_repo.setdefault(repo, []).append((created, image_id, tag))
    for repo, entries in by_repo.items():
        entries.sort(reverse=True)
        for created, image_id, tag in entries[KEEP_IMAGES_PER_REPO:]:
            if image_id in live:
                continue
            print(f"  image: rmi {repo}:{tag}")
            if not dry:
                sh(["docker", "rmi", image_id])
            removed += 1
    return removed


def cap_buildx(dry: bool) -> int:
    """Cap persistent buildx cache state volumes that grew past their budget."""
    pruned = 0
    for volume, cap_mb in BUILDX_STATE_CAP_MB.items():
        path = f"/var/lib/docker/volumes/{volume}/_data"
        try:
            size_mb = int(sh(["du", "-sm", path]).split()[0])
        except (IndexError, ValueError):
            continue
        if size_mb <= cap_mb:
            continue
        builder = volume.replace("buildx_buildkit_", "").replace("_state", "")
        print(f"  buildx: {builder} {size_mb}MB > {cap_mb}MB -> prune")
        if not dry:
            sh([
                "sudo", "-u", "runner", "docker", "buildx", "prune", "-f",
                "--reserved-space", f"{cap_mb}MB", "--builder", builder,
            ])
        pruned += 1
    return pruned


def warn(used_pct: float, thresholds: tuple[int, int], dry: bool) -> None:
    warn_pct, crit_pct = thresholds
    if used_pct < warn_pct:
        return
    level = "CRITICAL" if used_pct >= crit_pct else "WARNING"
    msg = f"CI VM disk {used_pct:.0f}% used ({level})"
    print(msg)
    if dry:
        return
    sh(["logger", "-t", "ci-disk-guard", msg])
    # Optional alert hook: drop a script at /usr/local/sbin/ci-disk-alert.sh
    # (e.g. curl to ntfy) and it is called with the level + message.
    alert = "/usr/local/sbin/ci-disk-alert.sh"
    try:
        if os.access(alert, os.X_OK):
            sh([alert, level, msg])
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--warn-pct", type=int, default=80)
    parser.add_argument("--crit-pct", type=int, default=90)
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("run as root: sudo python3 ci-disk-guard.py")

    before = free_pct()
    print(f"[{datetime.now(UTC).isoformat(timespec='seconds')}] disk {before:.0f}% used")
    containers = prune_containers(args.dry_run)
    volumes = prune_volumes(args.dry_run)
    images = prune_images(args.dry_run)
    buildx = cap_buildx(args.dry_run)
    after = free_pct()
    print(
        f"done: containers={containers} volumes={volumes} images={images} "
        f"buildx={buildx}; disk {before:.0f}% -> {after:.0f}% used"
    )
    warn(after, (args.warn_pct, args.crit_pct), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
