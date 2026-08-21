#!/usr/bin/env bash
# Rootless OpenSSH on WSL for Terraform remote-exec (self-host).
# Listens on 127.0.0.1:2222 — no sudo required.
set -euo pipefail

export PATH="/usr/sbin:/usr/bin:${PATH}"

SSH_DIR="${HOME}/.ssh"
SSHD_DIR="${SSH_DIR}/sshd-prodavan"
KEY_PATH="${SSH_DIR}/prodavan_tf"
AUTH_KEYS="${SSH_DIR}/authorized_keys"
PORT="${SSH_PORT:-2222}"
HOST="${SSH_BIND:-127.0.0.1}"
PID_FILE="${SSHD_DIR}/sshd.pid"
HOST_KEY="${SSHD_DIR}/host_ed25519"
CONFIG="${SSHD_DIR}/sshd_config"
LOG_FILE="${SSHD_DIR}/sshd.log"

mkdir -p "$SSH_DIR" "$SSHD_DIR"
chmod 700 "$SSH_DIR" "$SSHD_DIR"

if [[ ! -f "${KEY_PATH}" ]]; then
  ssh-keygen -t ed25519 -f "${KEY_PATH}" -N "" -C "prodavan-terraform@$(hostname)"
fi
chmod 600 "${KEY_PATH}"
chmod 644 "${KEY_PATH}.pub"

touch "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"
PUB="$(cat "${KEY_PATH}.pub")"
grep -qxF "$PUB" "$AUTH_KEYS" || echo "$PUB" >>"$AUTH_KEYS"

if [[ ! -f "$HOST_KEY" ]]; then
  ssh-keygen -t ed25519 -f "$HOST_KEY" -N "" -C "sshd-host-prodavan"
fi
chmod 600 "$HOST_KEY"

cat >"$CONFIG" <<EOF
Port ${PORT}
ListenAddress ${HOST}
HostKey ${HOST_KEY}
PidFile ${PID_FILE}
AuthorizedKeysFile ${AUTH_KEYS}
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes yes
AllowUsers $(whoami)
Subsystem sftp internal-sftp
EOF
chmod 600 "$CONFIG"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "sshd already running pid=$(cat "$PID_FILE") on ${HOST}:${PORT}"
else
  rm -f "$PID_FILE"
  /usr/sbin/sshd -f "$CONFIG" -E "$LOG_FILE"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      break
    fi
    sleep 0.2
  done
  if ! [[ -f "$PID_FILE" ]] && ! ss -ltn | grep -q ":${PORT}"; then
    echo "sshd failed to start; log:" >&2
    cat "$LOG_FILE" >&2 || true
    exit 1
  fi
  echo "Started rootless sshd on ${HOST}:${PORT}"
fi

echo "Private key: ${KEY_PATH}"
echo "Test: ssh -i ${KEY_PATH} -p ${PORT} -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${SSH_DIR}/known_hosts_prodavan $(whoami)@${HOST} 'echo ok'"
