#!/usr/bin/env bash
# Rendered by Terraform (k3s_server). Local CA + leaf cert for Traefik websecure.
# Non-empty SANS: generate (idempotent), install Secret+TLSStore into the k3s
# auto-apply manifests dir, export the CA for browser trust import.
# Run as root: sudo bash prodavan-tls.sh
set -euo pipefail

SANS='${https_tls_sans}'
if [ -z "$SANS" ]; then
  echo prodavan-tls: skipped \(no https_tls_sans\)
  exit 0
fi

DIR=/var/lib/rancher/k3s/prodavan-tls
MANIFEST_DIR=/var/lib/rancher/k3s/server/manifests
SSH_HOME='${ssh_home}'

mkdir -p "$DIR"
printf '%s\n' "$SANS" > "$DIR/.sans"

if [ ! -f "$DIR/ca.key" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj "/CN=Prodavan Dev CA" \
    -keyout "$DIR/ca.key" -out "$DIR/ca.crt"
  echo "prodavan-tls: CA created"
fi

if [ ! -f "$DIR/leaf.key" ] || [ ! -f "$DIR/leaf.crt" ] || ! cmp -s "$DIR/.sans" "$DIR/.sans.applied"; then
  openssl req -newkey rsa:2048 -nodes -subj "/CN=prodavan-dev" \
    -keyout "$DIR/leaf.key" -out "$DIR/leaf.csr"
  printf 'subjectAltName=%s\n' "$SANS" > "$DIR/.san.ext"
  openssl x509 -req -in "$DIR/leaf.csr" \
    -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial -days 825 \
    -extfile "$DIR/.san.ext" -out "$DIR/leaf.crt"
  cp "$DIR/.sans" "$DIR/.sans.applied"
  echo "prodavan-tls: leaf cert created (SANs: $SANS)"
else
  echo "prodavan-tls: leaf cert up to date"
fi

CRT_B64="$(base64 -w0 < "$DIR/leaf.crt")"
KEY_B64="$(base64 -w0 < "$DIR/leaf.key")"
cat > "$MANIFEST_DIR/prodavan-tls.yaml" <<EOF
# Managed by terraform (k3s-dev-host/prodavan-tls.sh.tpl). Do not edit.
apiVersion: v1
kind: Secret
metadata:
  name: prodavan-tls
  namespace: kube-system
type: kubernetes.io/tls
data:
  tls.crt: $CRT_B64
  tls.key: $KEY_B64
---
apiVersion: traefik.io/v1alpha1
kind: TLSStore
metadata:
  name: default
  namespace: kube-system
spec:
  defaultCertificate:
    secretName: prodavan-tls
EOF
chmod 600 "$MANIFEST_DIR/prodavan-tls.yaml"
echo "prodavan-tls: Secret kube-system/prodavan-tls + TLSStore default written"

if [ -n "$SSH_HOME" ] && [ -d "$SSH_HOME" ]; then
  install -o "$(stat -c %U "$SSH_HOME")" -g "$(stat -c %G "$SSH_HOME")" -m 644 \
    "$DIR/ca.crt" "$SSH_HOME/prodavan-dev-ca.crt"
  echo "prodavan-tls: CA for browser trust -> $SSH_HOME/prodavan-dev-ca.crt"
fi
