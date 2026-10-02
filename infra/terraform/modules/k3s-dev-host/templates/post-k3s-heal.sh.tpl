#!/usr/bin/env bash
# After k3s start: unstick Recreate deployments and rebind Traefik hostPort ${http_port}.
set -uo pipefail
KCTL="sudo -n /usr/local/bin/k3s kubectl"
HTTP_PORT="${http_port}"

wait_node() {
  for _ in $(seq 1 90); do
    if $KCTL get nodes --no-headers 2>/dev/null | grep -q ' Ready'; then
      return 0
    fi
    sleep 2
  done
  return 1
}

delete_stuck() {
  for ns in agentscale agentscale-dev-sandboxes; do
    while read -r name phase; do
      [ -z "$name" ] && continue
      case "$phase" in
        Terminating|Unknown|Failed|Error)
          echo "delete $ns/$name ($phase)"
          $KCTL delete pod -n "$ns" "$name" --grace-period=0 --force --wait=false 2>/dev/null || true
          ;;
      esac
    done < <($KCTL get pods -n "$ns" --no-headers 2>/dev/null | awk '{print $1, $3}')
  done
}

wait_node || echo "WARN: node not Ready yet"
delete_stuck
$KCTL -n kube-system delete pod -l app.kubernetes.io/name=traefik --wait=false 2>/dev/null || true

for _ in $(seq 1 72); do
  code=$(curl -sS -m 3 -o /dev/null -w '%%{http_code}' -H 'Host: localhost' "http://127.0.0.1:$${HTTP_PORT}/health/live" 2>/dev/null || echo 000)
  if [ "$code" = "200" ]; then
    echo "post-k3s-heal smoke OK"
    exit 0
  fi
  sleep 5
done
echo "WARN: post-k3s-heal smoke not ready (non-fatal)"
exit 0
