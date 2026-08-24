[Unit]
Description=prodavan terraform sshd (${ssh_port})
After=network.target

[Service]
Type=simple
ExecStart=/usr/sbin/sshd -f /home/${ssh_user}/.ssh/sshd-prodavan/sshd_config -D -E /home/${ssh_user}/.ssh/sshd-prodavan/sshd.log
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
