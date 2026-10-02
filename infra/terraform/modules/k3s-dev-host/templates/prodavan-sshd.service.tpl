[Unit]
Description=agentscale terraform sshd (${ssh_port})
After=network.target

[Service]
Type=simple
ExecStart=/usr/sbin/sshd -f /home/${ssh_user}/.ssh/sshd-agentscale/sshd_config -D -E /home/${ssh_user}/.ssh/sshd-agentscale/sshd.log
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
