Port 2222
# 0.0.0.0 so Windows can reach WSL eth0 via portproxy (127.0.0.1-only breaks Terraform from Win).
ListenAddress 0.0.0.0
HostKey /home/www/.ssh/sshd-prodavan/host_ed25519
PidFile /home/www/.ssh/sshd-prodavan/sshd.pid
AuthorizedKeysFile /home/www/.ssh/authorized_keys
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes yes
AllowUsers www
Subsystem sftp internal-sftp
