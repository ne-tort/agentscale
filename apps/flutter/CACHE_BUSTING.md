# Flutter web deploy cache busting

Flutter web ships a **service worker** + `version.json`. Updates only reach users if:

1. Build embeds a new **build number / BUILD_ID** (so `version.json` changes).
2. Nginx does **not** long-cache `index.html`, `flutter_service_worker.js`, `version.json`.
3. Kubernetes actually pulls the new image (`imagePullPolicy: Always` and/or SHA tags).

## Build identity

Dockerfile / CI:

```text
--build-name=1.0.0
--build-number=<github.run_number>
--dart-define=BUILD_ID=<git sha>
--dart-define=BUILD_NAME=...
--dart-define=BUILD_NUMBER=...
```

Login screen shows `buildLabel` (e.g. `1.0.0+42 · abcdef123456`) so you can verify the live bundle.

## Hash-based SW invalidation (index.html probe)

Even with `no-store` entrypoints the old service worker keeps serving the
previous bundle until its replacement activates (all tabs closed / reload
twice). `web/index.html` embeds a probe that closes this gap:

1. Active build hash — `navigator.serviceWorker.controller.scriptURL`
   (`flutter_service_worker.js?v=<hash>`).
2. Served build hash — `flutter_bootstrap.js?swprobe=<ts>` fetched
   `cache: no-store` + a cache-busting query (SW precache ignores unknown
   params → real network response); the bootstrap text contains
   `serviceWorkerVersion: "<hash>"`.
3. Mismatch → unregister all service workers, `caches.delete()` every cache,
   `location.reload()` — the next load boots the fresh bundle and registers
   the new worker.

Checks run 4s after load, every 5 minutes, on tab focus and on
visibilitychange. After any deploy an open tab switches to the new build
automatically (one silent reload).

## Nginx

See `apps/flutter/nginx.conf`: entrypoints `no-store`; `/assets/` and `/canvaskit/` immutable.
