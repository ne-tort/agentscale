#!/usr/bin/env bash
# Set imagePullPolicy=IfNotPresent on all containers/initContainers in argocd deploy+sts,
# then force-recreate pods so the policy actually takes effect (STS does not always roll).
set -euo pipefail

NS="${1:-argocd}"

patch_one() {
  local kind="$1" name="$2"
  kubectl -n "$NS" get "$kind" "$name" -o json | python3 -c '
import json, sys
doc = json.load(sys.stdin)
spec = doc["spec"]["template"]["spec"]
for c in spec.get("containers") or []:
    c["imagePullPolicy"] = "IfNotPresent"
for c in spec.get("initContainers") or []:
    c["imagePullPolicy"] = "IfNotPresent"
md = doc["metadata"]
for k in ("resourceVersion", "uid", "creationTimestamp", "generation", "managedFields"):
    md.pop(k, None)
doc.pop("status", None)
json.dump(doc, sys.stdout)
' | kubectl -n "$NS" replace -f -
}

echo "==> Force IfNotPresent on ${NS} workloads"
kubectl -n "$NS" get deploy -o name 2>/dev/null | while read -r res; do
  echo "  patch ${res}"
  patch_one deploy "${res#deployment.apps/}"
done
kubectl -n "$NS" get sts -o name 2>/dev/null | while read -r res; do
  echo "  patch ${res}"
  patch_one sts "${res#statefulset.apps/}"
done

echo "==> Recreate pods so policy applies"
kubectl -n "$NS" delete pods --all --force --grace-period=0 2>/dev/null || true
sleep 3
