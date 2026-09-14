/// Helpers for meta `scope.chats` / `scope.active_chat`.
library;

/// Synthetic session when no agent chat is open (matches API `DEFAULT_CHAT_SESSION_ID`).
const kDefaultChatSessionId = 'main';

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

/// Opt-in: hide tab / hub tile when there is no live agent chat.
bool scopeRequiresActiveChat(Map<String, dynamic>? entity) {
  if (entity == null) return false;
  final scope = entity['scope'];
  if (scope is! Map) return false;
  return '${scope['active_chat']}'.trim().toLowerCase() == 'required';
}

/// Whether [entity] should appear in nav/hub for the current session.
bool scopeVisibleForSession(Map<String, dynamic>? entity, String? sessionId) {
  if (!scopeRequiresActiveChat(entity)) return true;
  return sessionId != null && sessionId.trim().isNotEmpty;
}

String effectiveChatSessionId(String? sessionId) {
  final sid = sessionId?.trim();
  if (sid == null || sid.isEmpty) return kDefaultChatSessionId;
  return sid;
}
