#!/usr/bin/env bash
# Rendered by Terraform (k3s_server). Local CA + leaf cert for Traefik websecure.
# Non-empty SANS: generate (idempotent), install Secret+TLSStore into the k3s
# auto-apply manifests dir, export the CA for browser trust import.
# Run as root: sudo bash agentscale-tls.sh
set -euo pipefail

SANS='${https_tls_sans}'
if [ -z "$SANS" ]; then
  echo agentscale-tls: skipped \(no https_tls_sans\)
  exit 0
fi

DIR=/var/lib/rancher/k3s/agentscale-tls
MANIFEST_DIR=/var/lib/rancher/k3s/server/manifests
SSH_HOME='${ssh_home}'

# Bump when the extension set below changes: it forces a leaf re-issue so an
# existing install does not keep a certificate generated under the old rules.
EXT_VERSION=v2

mkdir -p "$DIR"
printf '%s\n' "$SANS" > "$DIR/.sans"

# A CA without ``keyUsage`` is rejected by Windows SChannel with
# "CA cert does not include key usage extension" (CERT_E... code 92), so a
# browser on Windows refuses the chain even after the CA is imported into
# Trusted Root -- and `.dev` is HSTS-preloaded, so there is no "proceed
# anyway" button to fall back on. ``openssl req -x509`` does not add keyUsage
# on its own; these extensions are the whole difference.
CA_EXT_ARGS=(
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0"
  -addext "keyUsage=critical,keyCertSign,cRLSign"
  -addext "subjectKeyIdentifier=hash"
)

ca_reissue=0
if [ ! -f "$DIR/ca.key" ]; then
  openssl genrsa -out "$DIR/ca.key" 2048
  ca_reissue=1
elif [ ! -f "$DIR/ca.crt" ] || ! openssl x509 -in "$DIR/ca.crt" -noout -text \
     | grep -q "X509v3 Key Usage"; then
  # Re-issue from the SAME key: subjectKeyIdentifier is derived from the key,
  # so leaves signed earlier stay valid against the re-issued CA.
  ca_reissue=1
fi

if [ "$ca_reissue" -eq 1 ]; then
  openssl req -x509 -new -key "$DIR/ca.key" -days 3650 \
    -subj "/CN=Agentscale Dev CA" \
    "$${CA_EXT_ARGS[@]}" \
    -out "$DIR/ca.crt"
  echo "agentscale-tls: CA issued with keyUsage (browser-trustable)"
fi

leaf_stale=0
if [ ! -f "$DIR/leaf.key" ] || [ ! -f "$DIR/leaf.crt" ]; then
  leaf_stale=1
elif ! cmp -s "$DIR/.sans" "$DIR/.sans.applied"; then
  leaf_stale=1
elif [ "$(cat "$DIR/.ext.version" 2>/dev/null || true)" != "$EXT_VERSION" ]; then
  leaf_stale=1
fi

if [ "$leaf_stale" -eq 1 ]; then
  openssl req -newkey rsa:2048 -nodes -subj "/CN=agentscale-dev" \
    -keyout "$DIR/leaf.key" -out "$DIR/leaf.csr"
  cat > "$DIR/.leaf.ext" <<LEAFEXT
basicConstraints=critical,CA:FALSE
subjectAltName=$SANS
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
LEAFEXT
  openssl x509 -req -in "$DIR/leaf.csr" \
    -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial -days 825 \
    -extfile "$DIR/.leaf.ext" -out "$DIR/leaf.crt"
  cp "$DIR/.sans" "$DIR/.sans.applied"
  printf '%s\n' "$EXT_VERSION" > "$DIR/.ext.version"
  echo "agentscale-tls: leaf cert created (SANs: $SANS)"
else
  echo "agentscale-tls: leaf cert up to date"
fi

CRT_B64="$(base64 -w0 < "$DIR/leaf.crt")"
KEY_B64="$(base64 -w0 < "$DIR/leaf.key")"
cat > "$MANIFEST_DIR/agentscale-tls.yaml" <<EOF
# Managed by terraform (k3s-dev-host/agentscale-tls.sh.tpl). Do not edit.
apiVersion: v1
kind: Secret
metadata:
  name: agentscale-tls
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
    secretName: agentscale-tls
EOF
chmod 600 "$MANIFEST_DIR/agentscale-tls.yaml"
echo "agentscale-tls: Secret kube-system/agentscale-tls + TLSStore default written"

if [ -n "$SSH_HOME" ] && [ -d "$SSH_HOME" ]; then
  install -o "$(stat -c %U "$SSH_HOME")" -g "$(stat -c %G "$SSH_HOME")" -m 644 \
    "$DIR/ca.crt" "$SSH_HOME/agentscale-dev-ca.crt"
  echo "agentscale-tls: CA for browser trust -> $SSH_HOME/agentscale-dev-ca.crt"
fi
