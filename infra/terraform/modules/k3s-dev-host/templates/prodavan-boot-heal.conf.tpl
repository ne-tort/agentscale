[Service]
TimeoutStopSec=30
ExecStartPre=-/usr/local/lib/prodavan/k3s-preflight.sh
# Background: a long synchronous ExecStartPost keeps the unit in start-post; on Win10
# WSL InitTerminate then force-powers off mid-heal and leaves Unknown pods.
ExecStartPost=-/bin/bash -c '/usr/local/lib/prodavan/post-k3s-heal.sh >>/var/log/prodavan-post-k3s-heal.log 2>&1 &'
