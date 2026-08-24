#!/usr/bin/env bash
# MinIO PVC retain: put object via API ObjectStorageManager → delete minio pod → get same bytes.
# Uses the app S3 path (not mc in the server image) so credentials/bucket match runtime.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
POD="${MINIO_POD:-prodavan-minio-0}"
MARKER="persist-$(date +%s)-$$"
KEY="ops/persist/${MARKER}.txt"

kubectl -n "$NS" wait --for=condition=Ready "pod/${POD}" --timeout=120s >/dev/null
kubectl -n "$NS" wait --for=condition=Available deploy/prodavan-api --timeout=180s >/dev/null

echo "==> put s3://${KEY} via API ObjectStorageManager"
kubectl -n "$NS" exec -i deploy/prodavan-api -- python - <<PY
import asyncio
from prodavan.config.settings import settings
from prodavan.core.infra.object_storage_manager import ObjectStorageManager

MARKER = "${MARKER}"
KEY = "${KEY}"

async def main() -> None:
    mgr = ObjectStorageManager(
        backend="s3",
        storage_root=settings.storage_root,
        s3_endpoint_url=settings.s3_endpoint_url,
        s3_access_key=settings.s3_access_key,
        s3_secret_key=settings.s3_secret_key,
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        mirror_local=False,
        required=True,
    )
    await mgr.startup()
    if mgr.backend_name != "s3":
        raise SystemExit(f"FAIL: backend={mgr.backend_name} (expected s3)")
    mgr.put_bytes_sync(KEY, MARKER.encode("utf-8"), content_type="text/plain")
    print("ok put", KEY, "bytes", len(MARKER))
    await mgr.shutdown()

asyncio.run(main())
PY

echo "==> delete ${POD} (PVC must retain object)"
kubectl -n "$NS" delete "pod/${POD}" --wait=true
kubectl -n "$NS" wait --for=condition=Ready "pod/${POD}" --timeout=240s
# Do not wait Deployment Available: Recreate + Argo image churn can stay false while
# the previous API pod is still serving, or while a SHA-pin ImagePullBackOff exists (I18).
echo "==> wait API object-store ready via ingress"
export SMOKE_ATTEMPTS="${SMOKE_ATTEMPTS:-36}"
export SMOKE_SLEEP_SEC="${SMOKE_SLEEP_SEC:-5}"
bash "${SCRIPT_DIR}/smoke_ingress.sh" >/dev/null
# MinIO blip can bounce API (OBJECT_STORE_REQUIRED + Recreate); wait for a live container.
kubectl -n "$NS" wait --for=condition=Ready pod -l app.kubernetes.io/name=prodavan-api --timeout=240s >/dev/null

echo "==> get same object after reschedule"
got=""
for i in 1 2 3 4 5 6; do
  if got="$(kubectl -n "$NS" exec -i deploy/prodavan-api -- python - <<PY
import asyncio
from prodavan.config.settings import settings
from prodavan.core.infra.object_storage_manager import ObjectStorageManager

KEY = "${KEY}"

async def main() -> None:
    mgr = ObjectStorageManager(
        backend="s3",
        storage_root=settings.storage_root,
        s3_endpoint_url=settings.s3_endpoint_url,
        s3_access_key=settings.s3_access_key,
        s3_secret_key=settings.s3_secret_key,
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        mirror_local=False,
        required=True,
    )
    await mgr.startup()
    raw = mgr.get_bytes_sync(KEY)
    print(raw.decode("utf-8"), end="")
    mgr.delete_sync(KEY)
    await mgr.shutdown()

asyncio.run(main())
PY
)"; then
    break
  fi
  sleep 5
  kubectl -n "$NS" wait --for=condition=Ready pod -l app.kubernetes.io/name=prodavan-api --timeout=120s >/dev/null || true
done
got="$(printf '%s' "$got" | tr -d '\r')"
if [[ "$got" != "$MARKER" ]]; then
  echo "FAIL: MinIO get after reschedule got=${got} want=${MARKER}" >&2
  exit 1
fi
echo "ok MinIO PVC retained ${MARKER}"
echo "verify_minio_pvc_retain OK"
