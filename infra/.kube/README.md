# Host kubeconfig (not in git)

Place k3s kubeconfig at `~/.kube/prodavan-dev.yaml` on the runner/operator host.

```bash
sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/prodavan-dev.yaml
# fix server URL to https://127.0.0.1:6443 if needed
export KUBECONFIG=~/.kube/prodavan-dev.yaml
```

Used by `prodavan-ops wait/smoke` and Verify Dev (resolved from `$HOME`, not from checkout).
