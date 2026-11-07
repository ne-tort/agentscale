# TypeScript web app → nginx (build context = repo root).
#
# Replaces the Flutter web image (apps/flutter/Dockerfile). The build is the
# same command CI runs (`pnpm --filter @agentscale/web build`, i.e. `tsc --noEmit
# && vite build`), so the image cannot contain code the gate did not typecheck.
#
# Node 22 and pnpm 10.30.3 match `.github/workflows/ci-web.yml`; the lockfile is
# `lockfileVersion: '9.0'`, which pnpm 9+ writes and pnpm 8 cannot read, so the
# version has to be pinned rather than floating.

# --- build -----------------------------------------------------------------
FROM node:22-alpine AS build

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=true

RUN corepack enable && corepack prepare pnpm@10.30.3 --activate

WORKDIR /repo

# Only the manifests first: editing a source file must not invalidate the
# dependency layer.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages ./packages

# `--frozen-lockfile` fails the build if the lockfile disagrees with the
# manifests, which is the same guarantee CI's install gives.
RUN pnpm install --frozen-lockfile

COPY apps/web ./apps/web
RUN pnpm --filter @agentscale/web build

# --- runtime ---------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/health || exit 1
