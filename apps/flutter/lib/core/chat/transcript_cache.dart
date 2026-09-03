import 'package:prodavan/core/chat/models/chat_block.dart';

/// In-memory stale-while-revalidate cache for chat transcript tails.
class TranscriptCacheEntry {
  const TranscriptCacheEntry({
    required this.sessionId,
    required this.blocks,
    required this.hasMoreHistory,
    required this.oldestSeq,
    required this.newestSeq,
    required this.totalEvents,
    required this.pendingApprovals,
  });

  final String? sessionId;
  final List<ChatBlock> blocks;
  final bool hasMoreHistory;
  final int? oldestSeq;
  final int? newestSeq;
  final int? totalEvents;
  final List<Map<String, dynamic>> pendingApprovals;
}

class TranscriptCache {
  TranscriptCache._();

  static final Map<String, TranscriptCacheEntry> _store = {};

  static String _key(String projectId, {String? sessionId}) => '$projectId:${sessionId ?? ''}';

  static TranscriptCacheEntry? get(String projectId, {String? sessionId}) {
    return _store[_key(projectId, sessionId: sessionId)];
  }

  static TranscriptCacheEntry? getForProject(String projectId) {
    final prefix = '$projectId:';
    for (final entry in _store.entries) {
      if (entry.key.startsWith(prefix)) return entry.value;
    }
    return null;
  }

  static void put(String projectId, TranscriptCacheEntry entry) {
    _store[_key(projectId, sessionId: entry.sessionId)] = entry;
    _store[_key(projectId)] = entry;
  }

  static void clearProject(String projectId) {
    _store.removeWhere((key, _) => key.startsWith('$projectId:'));
  }
}
