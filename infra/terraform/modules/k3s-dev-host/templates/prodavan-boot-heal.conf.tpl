[Service]
TimeoutStopSec=30
ExecStartPre=-/usr/local/lib/prodavan/k3s-preflight.sh
ExecStartPost=-/usr/local/lib/prodavan/post-k3s-heal.sh
