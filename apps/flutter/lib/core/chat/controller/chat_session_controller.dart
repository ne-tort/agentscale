import 'dart:async';
import 'dart:convert';

import 'package:prodavan/core/api/agent_stream_error.dart';
import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';
import 'package:prodavan/core/chat/transcript_cache.dart';

/// Live SSE chat session — blocks projection with optimistic user + streaming assistant.
class ChatSessionController {
  ChatSessionController({
    required this.api,
    required this.projectId,
    required this.sessionId,
    this.onSessionCreated,
  }) {
    _restoreFromCache();
  }

  final ProdavanApi api;
  final String projectId;
  String sessionId;
  final void Function(String sessionId)? onSessionCreated;
  String? selectedModel;
  final List<ChatBlock> blocks = [];
  bool streaming = false;
  Object? error;
  List<Map<String, dynamic>> pendingApprovals = const [];
  List<Map<String, dynamic>> availableModels = const [];
  String? defaultModel;
  bool hasMoreHistory = false;
  int? oldestSeq;
  int? newestSeq;
  int? totalEvents;
  bool loadingHistory = false;
  bool refreshingTranscript = false;
  bool hasCachedTranscript = false;

  String get selectedModelLabel {
    final id = selectedModel ?? defaultModel;
    if (id == null || id.isEmpty) return id ?? '';
    for (final m in availableModels) {
      final mid = m['id'] as String? ?? m['label'] as String? ?? '';
      if (mid == id) return m['label'] as String? ?? mid;
    }
    return id;
  }

  ProjectChatStreamHandle? _handle;
  List<ChatBlock> _liveTurnBlocks = const [];
  final _tick = StreamController<void>.broadcast();
  Timer? _notifyTimer;

  Stream<void> get changes => _tick.stream;

  void notify() {
    if (_tick.isClosed) return;
    _notifyTimer?.cancel();
    _notifyTimer = Timer(const Duration(milliseconds: 24), () {
      if (!_tick.isClosed) _tick.add(null);
    });
  }

  void notifyImmediate() {
    if (!_tick.isClosed) _tick.add(null);
  }

  void _restoreFromCache() {
    if (sessionId.isEmpty) return;
    final cached = TranscriptCache.get(projectId, sessionId);
    if (cached == null || cached.blocks.isEmpty) return;
    blocks
      ..clear()
      ..addAll(cached.blocks);
    hasMoreHistory = cached.hasMoreHistory;
    oldestSeq = cached.oldestSeq;
    newestSeq = cached.newestSeq;
    totalEvents = cached.totalEvents;
    pendingApprovals = cached.pendingApprovals;
    hasCachedTranscript = true;
  }

  void _saveToCache() {
    if (sessionId.isEmpty) return;
    TranscriptCache.put(
      projectId,
      TranscriptCacheEntry(
        sessionId: sessionId,
        blocks: List<ChatBlock>.from(blocks),
        hasMoreHistory: hasMoreHistory,
        oldestSeq: oldestSeq,
        newestSeq: newestSeq,
        totalEvents: totalEvents,
        pendingApprovals: List<Map<String, dynamic>>.from(pendingApprovals),
      ),
    );
    hasCachedTranscript = blocks.isNotEmpty;
  }

  Future<void> loadModels() async {
    try {
      final body = await api.listProjectModelsLive(projectId);
      final raw = body['models'];
      availableModels = raw is List ? raw.cast<Map<String, dynamic>>() : const [];
      defaultModel = body['default_model'] as String?;
      if (selectedModel == null || selectedModel!.isEmpty) {
        selectedModel = defaultModel;
      }
      error = null;
      notifyImmediate();
    } catch (e) {
      availableModels = const [];
      defaultModel = null;
      error = e;
      notifyImmediate();
    }
  }

  Future<void> loadTranscript({int? beforeSeq, bool background = false}) async {
    if (sessionId.isEmpty) {
      blocks.clear();
      _liveTurnBlocks = const [];
      hasMoreHistory = false;
      refreshingTranscript = false;
      notifyImmediate();
      return;
    }
    if (background) {
      refreshingTranscript = true;
      notifyImmediate();
    }
    try {
      final body = await api.projectChatTranscript(
        projectId: projectId,
        sessionId: sessionId,
        beforeSeq: beforeSeq,
      );
      final resolved = body['session_id'] as String?;
      if (resolved != null && resolved.isNotEmpty) sessionId = resolved;
      final newBlocks = chatBlocksFromTranscript(body['blocks'] as List?);
      if (beforeSeq == null) {
        blocks
          ..clear()
          ..addAll(newBlocks);
        _liveTurnBlocks = const [];
        final pending = body['pending_approvals'];
        pendingApprovals = pending is List ? pending.cast<Map<String, dynamic>>() : const [];
      } else {
        blocks.insertAll(0, newBlocks);
      }
      hasMoreHistory = body['has_more'] == true;
      oldestSeq = body['oldest_seq'] as int?;
      newestSeq = body['newest_seq'] as int?;
      final total = body['total_events'];
      if (total is int) {
        totalEvents = total;
      }
      if (beforeSeq == null) {
        _saveToCache();
      }
      notifyImmediate();
    } finally {
      if (background) {
        refreshingTranscript = false;
        notifyImmediate();
      }
    }
  }

  Future<void> loadOlderTranscript() async {
    if (!hasMoreHistory || loadingHistory || oldestSeq == null) return;
    loadingHistory = true;
    notifyImmediate();
    try {
      await loadTranscript(beforeSeq: oldestSeq);
    } finally {
      loadingHistory = false;
      notifyImmediate();
    }
  }

  Future<List<Map<String, dynamic>>> _fetchPending() async {
    return api.listPendingApprovals(projectId: projectId, sessionId: sessionId);
  }

  Future<void> send(String text, {List<String> attachmentRefs = const []}) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty && attachmentRefs.isEmpty) return;
    error = null;
    streaming = true;

    if (sessionId.isEmpty) {
      try {
        final created = await api.createAgentSession(
          projectId: projectId,
          model: selectedModel,
        );
        final sid = created['id'] as String?;
        if (sid == null || sid.isEmpty) {
          error = StateError('failed to create chat session');
          streaming = false;
          notifyImmediate();
          return;
        }
        sessionId = sid;
        onSessionCreated?.call(sid);
      } catch (e) {
        error = e;
        streaming = false;
        notifyImmediate();
        return;
      }
    }

    final userBlock = ChatBlock(
      kind: 'user',
      raw: {
        'text': trimmed,
        if (attachmentRefs.isNotEmpty) 'attachment_refs': attachmentRefs,
      },
    );
    _liveTurnBlocks = [userBlock];
    notifyImmediate();

    _handle?.abort();
    _handle = api.projectChatStream(
      projectId: projectId,
      text: trimmed,
      sessionId: sessionId,
      model: selectedModel,
      attachmentRefs: attachmentRefs,
    );

    try {
      await for (final event in _handle!.stream) {
        final type = event['type'] as String?;
        final data = event['data'];
        if (type == '_session' && data is Map<String, dynamic>) {
          final next = data['session_id'] as String?;
          if (next != null && next.isNotEmpty) {
            sessionId = next;
            onSessionCreated?.call(next);
          }
        } else if (type == '_turn_complete' && data is Map<String, dynamic>) {
          final next = data['session_id'] as String?;
          if (next != null && next.isNotEmpty) {
            sessionId = next;
            onSessionCreated?.call(next);
          }
          final pending = data['pending_approvals'];
          if (pending is List) pendingApprovals = pending.cast<Map<String, dynamic>>();
          _liveTurnBlocks = finalizeTurnBlocks(_liveTurnBlocks);
        } else if (type == '_error' && data is Map<String, dynamic>) {
          error = ProdavanApiException(
            data['status'] is int ? data['status'] as int : 503,
            jsonEncode({
              'code': data['code'],
              'title': data['title'],
              'detail': data['detail'],
            }),
          );
        } else if (type == 'error') {
          if (data is Map<String, dynamic>) {
            error = AgentStreamError(data);
          } else if (data is Map) {
            error = AgentStreamError(Map<String, dynamic>.from(data));
          } else {
            error = AgentStreamError({'message': event.toString()});
          }
        } else {
          _liveTurnBlocks = applyStreamEvent(_liveTurnBlocks, event);
          if (type == 'text_delta' || type == 'thinking_delta') {
            notifyImmediate();
          } else {
            notify();
          }
          continue;
        }
        notify();
      }
      blocks.addAll(_liveTurnBlocks);
      _liveTurnBlocks = const [];
      if (pendingApprovals.isEmpty) {
        pendingApprovals = await _fetchPending();
      }
      _saveToCache();
    } on ProdavanApiException catch (e) {
      error = e;
      notifyImmediate();
    } catch (e) {
      error = e;
      notifyImmediate();
    } finally {
      streaming = false;
      notifyImmediate();
    }
  }

  List<ChatBlock> get visibleBlocks => [...blocks, ..._liveTurnBlocks];

  Future<void> cancelStream() async {
    _handle?.abort();
    _liveTurnBlocks = finalizeTurnBlocks(_liveTurnBlocks, cancelled: true);
    blocks.addAll(_liveTurnBlocks);
    _liveTurnBlocks = const [];
    streaming = false;
    if (sessionId.isNotEmpty) {
      await api.cancelAgentSession(projectId: projectId, sessionId: sessionId);
    }
    _saveToCache();
    notifyImmediate();
  }

  Future<void> resolveApproval(String approvalId, String decision) async {
    await api.resolveToolApproval(
      projectId: projectId,
      sessionId: sessionId,
      approvalId: approvalId,
      decision: decision,
    );
    await loadTranscript();
  }

  void dispose() {
    _handle?.abort();
    _notifyTimer?.cancel();
    _tick.close();
  }
}
