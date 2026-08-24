/// Shared compile-time API base for all Flutter shells (employee / admin / company).
///
/// k3s web image: `--dart-define=API_BASE=http://prodavan.local:8088/api/v1`
library;

abstract final class ApiBase {
  static const String value = String.fromEnvironment(
    'API_BASE',
    defaultValue: 'http://127.0.0.1:8000/api/v1',
  );
}
