# Argo CD bootstrap for Prodavan (dev)

Install Argo into the cluster, then apply the Application that syncs `infra/k3s/overlays/dev`.

```bash
# Install Argo CD (once)
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Wait
kubectl -n argocd rollout status deployment/argocd-server

# Apply Prodavan app (edit repoURL if needed)
kubectl apply -f infra/argocd/apps/prodavan-dev.yaml
```

Or: `bash infra/scripts/argocd-bootstrap.sh`

CI pushes images to `ghcr.io/<owner>/prodavan-*` and bumps `infra/k3s/overlays/dev/kustomization.yaml`; Argo auto-syncs.
