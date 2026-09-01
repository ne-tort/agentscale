# Pull secrets (dev) — declarative only

Do **not** commit plaintext `ghcr-pull`. Do **not** mint secrets from CI.

Project sandbox Pods run in **`prodavan-sandboxes`** and need their own `ghcr-pull` (same credentials as `prodavan`).

1. Install Sealed Secrets controller once:
   `kubectl apply -k infra/argocd/sealed-secrets`
2. Create a local docker-registry secret, then seal it for **each** namespace:
   ```text
   kubectl -n prodavan create secret docker-registry ghcr-pull \
     --docker-server=ghcr.io --docker-username=USER --docker-password=TOKEN \
     --dry-run=client -o yaml \
   | kubeseal -o yaml > infra/k3s/overlays/dev/ghcr-pull.sealed.yaml

   kubectl -n prodavan-sandboxes create secret docker-registry ghcr-pull \
     --docker-server=ghcr.io --docker-username=USER --docker-password=TOKEN \
     --dry-run=client -o yaml \
   | kubeseal -o yaml > infra/k3s/overlays/dev/sandboxes/ghcr-pull.sealed.yaml
   ```
3. Add sealed files to the matching `kustomization.yaml` resources and merge via PR.
4. Templates without secrets: `ghcr-pull.sealed.yaml.example`, `sandboxes/ghcr-pull.sealed.yaml.example`.

Terraform bootstrap (`TF_VAR_ghcr_token`) also creates `ghcr-pull` in both namespaces on fresh cluster setup.

Argo + Sealed Secrets controller unseal into the cluster. Kubelet pulls from GHCR.
