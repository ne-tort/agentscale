/// `poll_while` contract from module ui_json (product module seeds).
///
/// Views may declare live polling while a row field keeps a value, e.g.
/// catalog indexing:
///
/// ```json
/// "poll_while": {"field": "status", "equals": "indexing", "interval_ms": 3000}
/// ```
///
/// Interpreters reload their data on [interval] while the condition matches
/// (with a hard runtime cap so a hung status cannot poll forever).
library;

class PollWhileConfig {
  const PollWhileConfig({
    required this.field,
    required this.equals,
    required this.interval,
  });

  /// Body field to watch (e.g. `status`).
  final String field;

  /// Value (or list of values) that keeps polling active
  /// (e.g. `indexing` or `["queued", "indexing"]`).
  final Object? equals;

  /// Reload interval.
  final Duration interval;

  bool matches(Object? actual) {
    final expected = equals;
    if (expected is List) {
      return expected.any((e) => actual?.toString() == e?.toString());
    }
    if (expected is bool) return (actual == true) == expected;
    return actual?.toString() == expected?.toString();
  }
}

/// Parses `ui_json.poll_while`; null when absent or malformed
/// (missing/empty field, non-positive interval).
PollWhileConfig? parsePollWhile(Object? raw) {
  if (raw is! Map) return null;
  final field = raw['field']?.toString();
  final intervalRaw = raw['interval_ms'];
  final intervalMs = intervalRaw is num
      ? intervalRaw.toInt()
      : int.tryParse(intervalRaw?.toString() ?? '');
  if (field == null || field.isEmpty || intervalMs == null || intervalMs <= 0) {
    return null;
  }
  return PollWhileConfig(
    field: field,
    equals: raw['equals'],
    interval: Duration(milliseconds: intervalMs),
  );
}
