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

## Nginx

See `apps/flutter/nginx.conf`: entrypoints `no-store`; `/assets/` and `/canvaskit/` immutable.
