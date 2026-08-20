# Flutter client

Спека: [`docs/04-frontend/architecture.md`](../../docs/04-frontend/architecture.md)

## Quick start

```bash
cd apps/flutter
flutter pub get
flutter analyze
flutter run -d chrome --dart-define=API_BASE=http://localhost:8000
```

## Structure (I0)

```text
lib/
├── app.dart
├── core/          # config, theme, network (stubs)
├── shell/         # NavGate, FeatureGate
└── features/      # auth skeleton; M00–M09 in later iterations
```
