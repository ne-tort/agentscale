/// Helpers for meta ``scope.chats`` (shared vs per-chat rows).
library;

bool tableIsChatScoped(Map<String, dynamic>? table) {
  if (table == null) return false;
  final scope = table['scope'];
  if (scope is! Map) return false;
  return '${scope['chats']}'.trim().toLowerCase() == 'current';
}

bool tableSlugIsChatScoped(Iterable<Map<String, dynamic>> tables, String tableSlug) {
  for (final t in tables) {
    if (t['slug'] == tableSlug) return tableIsChatScoped(t);
  }
  return false;
}
