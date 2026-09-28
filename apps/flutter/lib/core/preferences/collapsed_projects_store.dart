import 'package:shared_preferences/shared_preferences.dart';

/// Persisted set of collapsed project branch ids for the chats rail tree.
///
/// Empty set = every project branch is expanded (default for unseen
/// projects). Pure static — no instance state to wire.
class CollapsedProjectsStore {
  static const _key = 'prodavan.chats.rail.collapsedProjects';

  const CollapsedProjectsStore._();

  /// Load collapsed project ids (empty when nothing persisted or prefs are
  /// unavailable — collapse state is UI-only, never fatal).
  static Future<Set<String>> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getStringList(_key);
      return raw?.toSet() ?? <String>{};
    } catch (_) {
      return <String>{};
    }
  }

  /// Persist the full collapsed set (fire-and-forget from the shell).
  static Future<void> save(Set<String> ids) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_key, ids.toList());
    } catch (_) {
      // Persistence is best-effort — collapse state is UI-only.
    }
  }
}
