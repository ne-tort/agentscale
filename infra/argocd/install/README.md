# Argo CD install (vendored)

`upstream-install.yaml` is the official
[`argoproj/argo-cd` `v2.13.3` `manifests/install.yaml`](https://github.com/argoproj/argo-cd/blob/v2.13.3/manifests/install.yaml),
checked in so `prodavan-ops validate` / CI Gate do not fetch
`raw.githubusercontent.com` (flaky on self-hosted runners).

Refresh (when bumping the pin):

```bash
SHA=$(gh api repos/argoproj/argo-cd/contents/manifests/install.yaml?ref=v2.13.3 --jq .sha)
gh api "repos/argoproj/argo-cd/git/blobs/$SHA" --jq .content | python -c \
  "import sys,base64; open('upstream-install.yaml','wb').write(base64.b64decode(sys.stdin.read()))"
```

Then update the version comment in `kustomization.yaml`.
