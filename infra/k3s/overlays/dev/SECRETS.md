# Pull secrets (dev) — declarative only

Do **not** commit plaintext `ghcr-pull`. Do **not** mint secrets from CI.

1. Install Sealed Secrets controller once:
   `kubectl apply -k infra/argocd/sealed-secrets`
2. Create a local docker-registry secret, then seal it:
   ```text
   kubectl -n prodavan create secret docker-registry ghcr-pull \
     --docker-server=ghcr.io --docker-username=USER --docker-password=TOKEN \
     --dry-run=client -o yaml \
   | kubeseal -o yaml > infra/k3s/overlays/dev/ghcr-pull.sealed.yaml
   ```
3. Add `ghcr-pull.sealed.yaml` to `kustomization.yaml` resources and merge via PR.
4. Template without secrets: `ghcr-pull.sealed.yaml.example`.

Argo + Sealed Secrets controller unseal into the cluster. Kubelet pulls from GHCR.
