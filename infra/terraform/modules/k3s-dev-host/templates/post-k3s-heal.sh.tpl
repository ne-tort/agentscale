#!/usr/bin/env bash
# After k3s start: unstick Recreate deployments and rebind Traefik hostPort ${http_port}.
set -uo pipefail
KCTL="sudo -n /usr/local/bin/k3s kubectl"
HTTP_PORT="${http_port}"
DNS_UPSTREAMS="${dns_upstreams}"

# CoreDNS: k3s перезаписывает свой ConfigMap при КАЖДОМ старте (addon-контроллер),
# поэтому правка `forward .` руками живёт до ближайшей перезагрузки. Инцидент
# 2026-10-22: после рестарта сервера CoreDNS форвардил на мёртвый nameserver из
# хостового /etc/resolv.conf, и из подов не резолвилось НИ ОДНО внешнее имя —
# агент не мог достучаться до провайдера ИИ. 2026-10-23 то же повторилось:
# сохранённый вручную фикс слетел после ребута.
#
# Здесь фикс навязывается после каждого старта k3s — до тех пор, пока задан
# dns_upstreams (prod: DNS через туннель + публичные резервные). Пустое значение
# (dev) поведение не меняет: там хостовый резолвер рабочий.
ensure_coredns_forward() {
  [ -z "$DNS_UPSTREAMS" ] && return 0
  local corefile
  corefile=$($KCTL -n kube-system get cm coredns -o jsonpath='{.data.Corefile}' 2>/dev/null) || return 0
  [ -z "$corefile" ] && return 0
  case "$corefile" in
    *"$DNS_UPSTREAMS"*) return 0 ;;
  esac

  # Один python-проход: собрать Corefile с явным forward и JSON-патч для kubectl.
  # ВАЖНО: данные передаём ФАЙЛОМ, а не через stdin — `python3 - <<'PY'` уже
  # занимает stdin программой, и pipe в него не доходит (sys.stdin.read() пуст).
  printf '%s' "$corefile" > /tmp/coredns-corefile.txt
  if ! python3 - "$DNS_UPSTREAMS" /tmp/coredns-corefile.txt > /tmp/coredns-patch.json 2>/dev/null <<'PY'
import json
import sys

ups = sys.argv[1]
cf = open(sys.argv[2], encoding="utf-8").read()
old = "    forward . /etc/resolv.conf"
if old not in cf:
    sys.exit(1)
new = (
    "    # Явные upstream вместо /etc/resolv.conf: хостовый резолвер может\n"
    "    # подняться с мёртвым nameserver, и тогда внешние имена не резолвятся\n"
    "    # ни в одном поде (инциденты 2026-10-22 и 2026-10-23).\n"
    f"    forward . {ups} {{\n"
    "        policy sequential\n"
    "        max_fails 1\n"
    "        health_check 5s\n"
    "    }"
)
sys.stdout.write(json.dumps({"data": {"Corefile": cf.replace(old, new)}}))
PY
  then
    echo "WARN: CoreDNS forward не распознан — не трогаю"
    return 0
  fi

  $KCTL -n kube-system patch cm coredns --type merge --patch-file /tmp/coredns-patch.json >/dev/null 2>&1 || true
  $KCTL -n kube-system rollout restart deploy/coredns >/dev/null 2>&1 || true
  echo "CoreDNS forward -> $DNS_UPSTREAMS"
}

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
ensure_coredns_forward
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
