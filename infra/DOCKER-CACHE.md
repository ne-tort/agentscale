# Docker build cache (Prodavan)

Default `docker` driver **cannot** export `type=local` cache. Use the `prodavan` buildx builder (`docker-container`):

```bash
bash infra/scripts/docker-build-cached.sh ensure-builder
export BUILDX_BUILDER=prodavan DOCKER_BUILDKIT=1
```

Cache lives in gitignored `.docker-cache/{api,web}/`.

| Change | Rebuilt layers |
|--------|----------------|
| `apps/api/src/**` | COPY src + chmod only (`pip install` **CACHED**) |
| `apps/api/pyproject.toml` | pip install + later |
| `apps/flutter/lib/**` | flutter build (after `pub get` **CACHED**) |
| `apps/flutter/pubspec.*` | pub get + build |

Compose stack uses the same `cache_from` / `cache_to` paths.

Web image installs Flutter SDK from the official Google tarball
(`storage.googleapis.com/.../flutter_linux_*-stable.tar.xz`), not cirruslabs.

One-shot: `bash infra/scripts/stack-up.sh`
