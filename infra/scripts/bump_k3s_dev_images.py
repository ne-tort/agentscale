#!/usr/bin/env python3
"""Bump infra/k3s/overlays/dev image tags for Argo sync."""

from __future__ import annotations

import argparse
from pathlib import Path


TEMPLATE = """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: prodavan
resources:
  - ../../base
  - postgres.yaml
patches:
  - path: patch-dev.yaml
images:
  - name: ghcr.io/{owner}/prodavan-api
    newName: ghcr.io/{owner}/prodavan-api
    newTag: {tag}
  - name: ghcr.io/{owner}/prodavan-web
    newName: ghcr.io/{owner}/prodavan-web
    newTag: {tag}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--path",
        default="infra/k3s/overlays/dev/kustomization.yaml",
    )
    args = parser.parse_args()
    path = Path(args.path)
    path.write_text(
        TEMPLATE.format(owner=args.owner.lower(), tag=args.tag),
        encoding="utf-8",
    )
    print(f"Wrote {path} -> {args.owner}/{args.tag}")


if __name__ == "__main__":
    main()
