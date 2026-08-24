#!/usr/bin/env bash
# I8 slice 1: Job can mount API PVC (same path materialize uses). Not a per-project isolator yet.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
JOB="prodavan-sandbox-probe"
API_IMAGE="${API_IMAGE:-ghcr.io/ne-tort/prodavan-api:local}"

kubectl -n "$NS" get sa prodavan-sandbox >/dev/null \
  || { echo "FAIL: ServiceAccount prodavan-sandbox missing" >&2; exit 1; }

kubectl -n "$NS" delete job "$JOB" --ignore-not-found --wait=true >/dev/null 2>&1 || true

kubectl -n "$NS" apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
  namespace: ${NS}
  labels:
    app.kubernetes.io/name: prodavan-sandbox-probe
    app.kubernetes.io/part-of: prodavan
    app.kubernetes.io/component: sandbox-probe
spec:
  ttlSecondsAfterFinished: 120
  backoffLimit: 1
  activeDeadlineSeconds: 90
  template:
    metadata:
      labels:
        app.kubernetes.io/name: prodavan-sandbox-probe
        app.kubernetes.io/part-of: prodavan
        app.kubernetes.io/component: sandbox-probe
    spec:
      serviceAccountName: prodavan-sandbox
      restartPolicy: Never
      containers:
        - name: probe
          image: ${API_IMAGE}
          imagePullPolicy: IfNotPresent
          command:
            - python
            - -c
            - |
              import os, pathlib
              root = pathlib.Path("/data/storage")
              assert root.is_dir(), root
              projects = root / "projects"
              print("ok pvc", root, "projects_exists", projects.is_dir())
          volumeMounts:
            - name: storage
              mountPath: /data/storage
              readOnly: true
      volumes:
        - name: storage
          persistentVolumeClaim:
            claimName: prodavan-api-storage
EOF

kubectl -n "$NS" wait --for=condition=complete "job/${JOB}" --timeout=120s
kubectl -n "$NS" logs "job/${JOB}" | grep -q 'ok pvc' \
  || { echo "FAIL: sandbox job logs missing ok pvc" >&2; kubectl -n "$NS" logs "job/${JOB}"; exit 1; }
echo "ok Job ${JOB} mounted prodavan-api-storage (I8 slice 1 — API still object-ws, no spawn)"
kubectl -n "$NS" delete job "$JOB" --ignore-not-found --wait=true >/dev/null 2>&1 || true
echo "verify_sandbox_job OK"
