/// Shared API base for Flutter shells (employee / admin / company).
///
/// Web (k3s): prefer same-origin `/api/v1` so `127.0.0.1` and `localhost` both work.
/// Override with `--dart-define=API_BASE=...` when the API is on another host.
library;

import 'package:flutter/foundation.dart';

abstract final class ApiBase {
  static const String _fromEnv = String.fromEnvironment(
    'API_BASE',
    defaultValue: '',
  );

  /// Resolved base URL including `/api/v1` (no trailing slash required by callers that append paths).
  static String get value {
    if (_fromEnv.isNotEmpty) {
      // Explicit dart-define wins (native / special deploys).
      return _fromEnv;
    }
    if (kIsWeb) {
      final origin = Uri.base.origin;
      if (origin.isNotEmpty && origin != 'null') {
        return '$origin/api/v1';
      }
    }
    return 'http://127.0.0.1:8000/api/v1';
  }
}
