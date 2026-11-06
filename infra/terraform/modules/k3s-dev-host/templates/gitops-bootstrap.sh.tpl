#!/bin/bash
# Rendered by Terraform templatefile → file provisioner (gitops_bootstrap).
set -eu
trap 'rm -f /tmp/prodavan-ghcr.token /tmp/prodavan-gitops.sh' EXIT
export KUBECONFIG='${kubeconfig_path}'
export PATH=/usr/local/bin:/usr/bin:/bin
REPO='${remote_repo_path}'
KCTL=(sudo -n /usr/local/bin/k3s kubectl)
# gitops_apps=prod: dedicated prod server — только prod-контур (projects +
# agent-sandbox + agentscale-prod); all: общий хост (dev+e2e+prod).
GITOPS_APPS='${gitops_apps}'
if [ "$GITOPS_APPS" = prod ]; then
  PLATFORM_APP=agentscale-prod
  PLATFORM_NS=agentscale
  GHCR_NS="agentscale agentscale-sandboxes"
  ROOT_APP="$REPO/infra/argocd/root-app-prod.yaml"
else
  PLATFORM_APP=agentscale-dev
  PLATFORM_NS=agentscale-dev
  GHCR_NS="agentscale-dev agentscale-dev-sandboxes agentscale agentscale-sandboxes"
  ROOT_APP="$REPO/infra/argocd/root-app.yaml"
fi
test -d "$REPO/infra/argocd/install" || {
  echo "ERROR: Argo CD install dir not found: $REPO/infra/argocd/install" >&2
  echo "       terraform runs on the VM and bootstraps from the checkout on the VM." >&2
  echo "       Clone/update it: git -C $(dirname "$REPO") clone https://github.com/ne-tort/agentscale.git" >&2
  exit 1
}
# ghcr-pull / repo-agentscale are NOT in git (no SealedSecret for them). Without
# the token Argo cannot read the private repo and every image pull fails — the
# old behaviour was to print one WARN and then fail 12 minutes later on the
# platform timeout, which looked like a broken cluster instead of a missing
# secret. Fail here, with the exact command that fixes it.
if [ ! -s /tmp/prodavan-ghcr.token ]; then
  echo "ERROR: TF_VAR_ghcr_token is empty — nothing to create ghcr-pull / repo-agentscale from." >&2
  echo "       The repository is private, so Argo cannot sync and kubelet cannot pull images." >&2
  echo "       Re-run with a token that has read:packages, e.g. on the VM:" >&2
  echo "         export TF_VAR_ghcr_token=\"\$(gh auth token)\"" >&2
  echo "         terraform -chdir=infra/terraform/environments/vm apply -auto-approve" >&2
  exit 1
fi
sync="$("$${KCTL[@]}" -n argocd get application "$PLATFORM_APP" -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
health="$("$${KCTL[@]}" -n argocd get application "$PLATFORM_APP" -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
if [ "$sync" = Synced ] && [ "$health" = Healthy ]; then
  echo gitops-already-healthy
  exit 0
fi
# Cold VM on a slow link: argocd-server/repo-server images must be pulled first.
"$${KCTL[@]}" apply -k "$REPO/infra/argocd/install" || {
  echo "ERROR: kubectl apply -k infra/argocd/install failed" >&2
  exit 1
}
for dep in argocd-server argocd-repo-server; do
  if ! "$${KCTL[@]}" -n argocd wait --for=condition=Available "deployment/$dep" --timeout=600s; then
    echo "ERROR: argocd/$dep not Available after 600s" >&2
    "$${KCTL[@]}" -n argocd get pods -o wide || true
    "$${KCTL[@]}" -n argocd describe "deployment/$dep" | tail -30 || true
    exit 1
  fi
done
if [ -s /tmp/prodavan-ghcr.token ]; then
  "$${KCTL[@]}" -n argocd delete secret repo-agentscale --ignore-not-found
  "$${KCTL[@]}" -n argocd create secret generic repo-agentscale \
    --from-literal=type=git \
    --from-literal=url=https://github.com/ne-tort/agentscale.git \
    --from-literal=username=git \
    --from-file=password=/tmp/prodavan-ghcr.token
  "$${KCTL[@]}" -n argocd label secret repo-agentscale argocd.argoproj.io/secret-type=repository --overwrite
fi
"$${KCTL[@]}" apply -k "$REPO/infra/argocd/sealed-secrets"
"$${KCTL[@]}" -n kube-system wait --for=condition=Available deployment/sealed-secrets-controller --timeout=300s || {
  echo "ERROR: sealed-secrets-controller not Available" >&2
  "$${KCTL[@]}" -n kube-system get pods -l app.kubernetes.io/name=sealed-secrets -o wide || true
  exit 1
}
if [ -s /tmp/prodavan-ghcr.token ]; then
  # ghcr-pull секреты должны существовать ДО первого image pull;
  # Argo CreateNamespace создаёт ns сам, но секреты нужны заранее.
  for ns in $GHCR_NS; do
    "$${KCTL[@]}" create namespace "$ns" --dry-run=client -o yaml | "$${KCTL[@]}" apply -f -
    "$${KCTL[@]}" -n "$ns" delete secret ghcr-pull --ignore-not-found
    GHCR_PASS="$(cat /tmp/prodavan-ghcr.token)"
    "$${KCTL[@]}" -n "$ns" create secret docker-registry ghcr-pull \
      --docker-server=ghcr.io \
      --docker-username='${ghcr_username}' \
      --docker-password="$GHCR_PASS"
    unset GHCR_PASS
  done
fi
"$${KCTL[@]}" apply -f "$ROOT_APP"
# agent-sandbox app (sync-wave -5) must be Healthy before the platform app:
# the *-sandboxes layer needs agent-sandbox CRDs to exist. 48×10с = 8 мин:
# на холодной VM app-controller сначала тянет CRD-образы.
for _ in $(seq 1 48); do
  asb_sync="$("$${KCTL[@]}" -n argocd get application agent-sandbox -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Pending)"
  asb_health="$("$${KCTL[@]}" -n argocd get application agent-sandbox -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown)"
  echo "agent-sandbox sync=$asb_sync health=$asb_health"
  if [ "$asb_sync" = Synced ] && [ "$asb_health" = Healthy ]; then
    break
  fi
  sleep 10
done
if [ "$asb_sync" != Synced ] || [ "$asb_health" != Healthy ]; then
  echo "WARN: agent-sandbox не стала Healthy за 8 мин — платформа всё равно запускается," >&2
  echo "      но *-sandboxes слой без CRD не поднимется. Диагностика ниже." >&2
  "$${KCTL[@]}" -n argocd get application agent-sandbox -o yaml | tail -40 || true
fi
# Платформа стартует долго (keycloak/postgres/opensearch/redpanda/mongo): на
# холодной VM это ещё и pull всех образов. 108×10с = 18 мин; прежние 12 мин
# кончались на первом же чистом прогоне.
for _ in $(seq 1 108); do
  sync="$("$${KCTL[@]}" -n argocd get application "$PLATFORM_APP" -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Pending)"
  health="$("$${KCTL[@]}" -n argocd get application "$PLATFORM_APP" -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown)"
  echo "$PLATFORM_APP sync=$sync health=$health"
  if [ "$sync" = Synced ] && [ "$health" = Healthy ]; then
    exit 0
  fi
  sleep 10
done
# Провал здесь = apply падает, состояние остаётся в state как «создан» только до
# этого ресурса; повторный apply перезапускает ЭТОТ скрипт, а он идемпотентен
# (Argo уже стоит, секреты уже есть) и коротко выходит, если платформа успела
# подняться. Поэтому главное — дать диагноз, а не молчаливый exit 1.
echo "ERROR: $PLATFORM_APP не стала Synced+Healthy за 18 мин. Диагностика:" >&2
"$${KCTL[@]}" -n argocd get applications -o wide || true
echo "--- pods в $PLATFORM_NS (не Ready) ---" >&2
"$${KCTL[@]}" -n "$PLATFORM_NS" get pods -o wide || true
echo "--- последние события ---" >&2
"$${KCTL[@]}" -n "$PLATFORM_NS" get events --sort-by=.lastTimestamp 2>/dev/null | tail -25 || true
echo "--- условия Application ---" >&2
"$${KCTL[@]}" -n argocd get application "$PLATFORM_APP" -o jsonpath='{range .status.conditions[*]}{.type}: {.message}{"\n"}{end}' 2>/dev/null || true
echo "--- частые причины: ImagePullBackOff (нет ghcr-pull / протух токен)," >&2
echo "    CrashLoopBackOff у keycloak/postgres (не хватило памяти), Argo OutOfSync" >&2
echo "    (репозиторий недоступен: repo-agentscale). Повторный apply доводит до конца." >&2
exit 1
