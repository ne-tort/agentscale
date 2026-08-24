[Unit]
Description=prodavan terraform sshd (${ssh_port})
After=network.target

[Service]
User=${ssh_user}
ExecStart=/usr/sbin/sshd -f /home/${ssh_user}/.ssh/sshd-prodavan/sshd_config -D -E /home/${ssh_user}/.ssh/sshd-prodavan/sshd.log
Restart=always

[Install]
WantedBy=multi-user.target
