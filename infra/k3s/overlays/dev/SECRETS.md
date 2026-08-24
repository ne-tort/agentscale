# ghcr-pull is NOT committed as a plain Secret.
#
# Preferred (GitOps):
#   1. kubectl apply -k infra/argocd/sealed-secrets
#   2. Create a local docker-registry secret, then seal it:
#        kubectl -n prodavan create secret docker-registry ghcr-pull \
#          --docker-server=ghcr.io --docker-username=USER --docker-password=TOKEN \
#          --dry-run=client -o yaml \
#        | kubeseal -o yaml > infra/k3s/overlays/dev/ghcr-pull.sealed.yaml
#   3. Commit the SealedSecret; Argo syncs it; controller materializes Secret.
#
# Bootstrap (no kubeseal yet) — operator laptop only, never in CI Gate/Images:
#   export GHCR_TOKEN=… GHCR_USERNAME=…
#   poetry run prodavan-ops ensure-ghcr-secret
#
# Verify Dev workflow does NOT create this secret.
