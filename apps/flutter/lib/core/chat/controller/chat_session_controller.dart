import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/agent_stream_error.dart';
import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';
import 'package:prodavan/core/chat/transcript_cache.dart';

/// Live SSE chat session — blocks projection with optimistic user + streaming assistant.
class ChatSessionController {
  /// Silence watchdog (Wave 5): server keepalives every ~15s prove the path
  /// is alive; this budget of total silence (no events AND no keepalives)
  /// means a wedged runtime or a silently cut relay — abort with an honest
  /// error instead of an eternal "typing" state.
  static const _streamSilenceWatchdog = Duration(seconds: 90);

  /// The error surfaced when the silence watchdog aborts the stream.
  static AgentStreamError _bridgeTimeoutError() => AgentStreamError(const {
    'code': 'BRIDGE_TIMEOUT',
    'message': 'agent runtime silent: no events or keepalives',
  });

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

  /// Set when a live turn is interrupted by a connection drop (SSE break):
  /// the composer listens and restores the user text as an editable draft
  /// instead of losing the message.
  final ValueNotifier<String?> interruptedDraft = ValueNotifier<String?>(null);

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
        // Pagination may legitimately return zero new blocks near the head —
        // only short-circuit when there is nothing more to load.
        blocks.insertAll(0, newBlocks);
      }
      hasMoreHistory = body['has_more'] == true;
      oldestSeq = body['oldest_seq'] as int?;
      newestSeq = body['newest_seq'] as int?;
      final total = body['total_events'];
      if (total is int) {
        totalEvents = total;
      }
      // A page that returned blocks but reports has_more=false means we reached
      // the head — clear the stale oldestSeq so loadOlderTranscript gates out.
      if (!hasMoreHistory) {
        oldestSeq = null;
      }
      if (beforeSeq == null) {
        _saveToCache();
      }
      notifyImmediate();
    } catch (e) {
      // Best-effort transcript load: do not crash the chat. Keep the cached /
      // existing blocks visible; surface the error so the UI can show a
      // themed snackbar without leaving the composer in a dead state.
      error = e;
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
    } catch (e) {
      // loadTranscript already recorded the error; keep loadingHistory gating sane.
      error = e;
      notifyImmediate();
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
          if (trimmed.isNotEmpty) interruptedDraft.value = trimmed;
          notifyImmediate();
          return;
        }
        sessionId = sid;
        onSessionCreated?.call(sid);
      } catch (e) {
        error = e;
        streaming = false;
        if (trimmed.isNotEmpty) interruptedDraft.value = trimmed;
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
    final handle = api.projectChatStream(
      projectId: projectId,
      text: trimmed,
      sessionId: sessionId,
      model: selectedModel,
      attachmentRefs: attachmentRefs,
    );
    _handle = handle;

    // Silence watchdog: reset by every event AND by server keepalives; on
    // fire — abort the stream and report BRIDGE_TIMEOUT below.
    var watchdogFired = false;
    Timer? watchdog;
    void armWatchdog() {
      watchdog?.cancel();
      watchdog = Timer(_streamSilenceWatchdog, () {
        watchdogFired = true;
        handle.abort();
      });
    }

    handle.onKeepalive = armWatchdog;
    armWatchdog();

    try {
      await for (final event in handle.stream) {
        armWatchdog();
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
      if (watchdogFired) {
        // The turn never produced (or stopped producing) anything — mark it
        // interrupted and hand the user text back to the composer.
        error = _bridgeTimeoutError();
        _finalizeInterruptedTurn(trimmed);
      } else {
        blocks.addAll(_liveTurnBlocks);
        _liveTurnBlocks = const [];
        if (pendingApprovals.isEmpty) {
          pendingApprovals = await _fetchPending();
        }
        _saveToCache();
      }
    } on ProdavanApiException catch (e) {
      error = watchdogFired ? _bridgeTimeoutError() : e;
      _finalizeInterruptedTurn(trimmed);
      notifyImmediate();
    } catch (e) {
      error = watchdogFired ? _bridgeTimeoutError() : e;
      _finalizeInterruptedTurn(trimmed);
      notifyImmediate();
    } finally {
      watchdog?.cancel();
      streaming = false;
      notifyImmediate();
    }
  }

  /// Connection dropped mid-turn: keep the partial transcript (marked
  /// interrupted, the way [cancelStream] marks cancelled) so the optimistic
  /// user message is not lost, and hand the user text back to the composer
  /// as an editable draft.
  void _finalizeInterruptedTurn(String userText) {
    if (_liveTurnBlocks.isNotEmpty) {
      _liveTurnBlocks = finalizeTurnBlocks(_liveTurnBlocks, interrupted: true);
      blocks.addAll(_liveTurnBlocks);
      _liveTurnBlocks = const [];
      _saveToCache();
    }
    if (userText.trim().isNotEmpty) {
      interruptedDraft.value = userText;
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
      try {
        await api.cancelAgentSession(projectId: projectId, sessionId: sessionId);
      } catch (e) {
        // Best-effort server-side cancel: the local turn is already marked
        // cancelled; surface the failure via [error] so the UI can snack it
        // instead of crashing on an unhandled async error.
        error = e;
      }
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
    interruptedDraft.dispose();
  }
}
