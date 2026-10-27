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

  /// done-reasons that mean the turn FAILED (vs "completed"). Older runtimes
  /// swallow provider errors into done{reason} without an error frame — the
  /// UI must still surface the failure (Wave 6).
  static const _turnFailureReasons = {
    'api_error',
    'model_error',
    'prompt_too_long',
    'aborted_streaming',
  };

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

  /// Provider-error reconnect (chat error policy): the runtime waits the
  /// configured interval before re-invoking the model — the UI shows
  /// «Попытка реконнекта…» / «(n/y)…» while it does. `null` = no reconnect.
  int? reconnectAttempt;
  int? reconnectMaxAttempts;
  String? reconnectNextModel;
  List<Map<String, dynamic>> pendingApprovals = const [];
  List<Map<String, dynamic>> availableModels = const [];
  String? defaultModel;

  /// MCP tool display aliases (mcp_aliases module meta, keyed by canonical
  /// ``mcp.<server>.<tool>`` and bare tool names) — chat labels use them to
  /// show «Поиск товара…» instead of raw wire names.
  Map<String, String> mcpAliases = const {};
  bool hasMoreHistory = false;
  int? oldestSeq;
  int? newestSeq;
  int? totalEvents;
  bool loadingHistory = false;
  bool refreshingTranscript = false;
  bool hasCachedTranscript = false;

  /// Server-reported: the agent is still working on this session (a turn is
  /// in progress in the transcript). Set on transcript load so a client that
  /// reloaded or navigated back mid-turn knows to show the working indicator
  /// and poll for live updates instead of appearing idle.
  bool turnInProgress = false;

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

  /// Bumped when the *structure* of the transcript changes (blocks added,
  /// removed, tool paired, turn finalized). Widget caches (timeline grouping,
  /// entry widgets, checklist) key off this — text growth does not bump it.
  int _structureRevision = 0;
  int get structureRevision => _structureRevision;

  /// Bumped when text inside an existing block grows (streaming deltas). Only
  /// caches that hold block text need invalidating; grouping does not change.
  int _contentRevision = 0;
  int get contentRevision => _contentRevision;

  /// Cached concatenation of history + live turn. Rebuild views consult this
  /// instead of copying thousands of block references on every hover/frame.
  List<ChatBlock>? _visibleCache;

  /// Keys of blocks currently in [blocks] — O(1) collision guard when
  /// appending polled/paginated pages (a duplicate key corrupts the reverse
  /// ListView's element reuse).
  final Set<String> _blockKeys = <String>{};

  /// Delta coalescing: SSE providers emit one event per token. Applying and
  /// splitting per token made every frame O(turn blocks + answer length) and
  /// the answer concatenation O(n²). Buffer ORDERED runs and flush ~25 times/s
  /// — consecutive text/thinking chunks merge, but their interleaving (and the
  /// segment boundaries it implies) is preserved.
  final List<_PendingDelta> _pendingDeltas = [];
  Timer? _deltaFlushTimer;
  static const _deltaFlushInterval = Duration(milliseconds: 40);

  /// Live tail polling — used when the agent is working but this client is
  /// not the SSE owner (reload / navigation mid-turn).
  Timer? _livePollTimer;
  bool _polling = false;
  int _pollFailures = 0;
  static const _livePollInterval = Duration(milliseconds: 2500);
  static const _livePollMaxFailures = 5;

  /// Monotonic local id generator for turn idempotency keys.
  int _turnSeq = 0;
  String _newTurnId() =>
      't${DateTime.now().microsecondsSinceEpoch.toRadixString(36)}-${_turnSeq++}';

  /// Turn timing (live path): first assistant output moment and the moment
  /// the answer completed — stamped onto the assistant block as
  /// `created_at` / `turn_ms` when the turn finalizes.
  DateTime? _turnFirstOutputAt;
  DateTime? _turnEndedAt;
  final _tick = StreamController<void>.broadcast();
  Timer? _notifyTimer;

  /// Last tool call of the live turn that has no matching tool result yet.
  bool get hasPendingToolCall {
    var lastCallIdx = -1;
    for (var i = 0; i < _liveTurnBlocks.length; i++) {
      if (_liveTurnBlocks[i].kind == 'tool_call') lastCallIdx = i;
    }
    if (lastCallIdx < 0) return false;
    final callId = _liveTurnBlocks[lastCallIdx].id;
    if (callId.isEmpty) return true;
    return !_liveTurnBlocks.any(
      (b) => b.kind == 'tool_result' && b.id == callId,
    );
  }

  /// Any live block is actively streaming (text/thinking deltas arriving).
  ///
  /// Buffered-but-not-yet-flushed chunks count as streaming: otherwise the
  /// "agentscale работает…" spinner would blink back on for up to one flush
  /// interval after every token.
  bool get anyBlockStreaming =>
      _pendingDeltas.isNotEmpty || _liveTurnBlocks.any((b) => b.isStreaming);

  /// "agentscale работает…" — the turn is streaming but the agent is silent
  /// (right after send, between events): show the working indicator so the
  /// user sees the agent did not stop. Suppressed while a reconnect wait is
  /// shown instead («Попытка реконнекта…»).
  ///
  /// Also true when the agent is working on the session but this client is
  /// NOT the one streaming it (reloaded/navigated back mid-turn): the server
  /// reports [turnInProgress] and we poll — the user still sees it working.
  bool get agentWorking => streaming || turnInProgress;

  bool get showWorkingIndicator =>
      agentWorking &&
      reconnectAttempt == null &&
      !anyBlockStreaming &&
      !hasPendingToolCall;

  /// «Попытка реконнекта…» — a provider-error reconnect is in flight. Works
  /// both while this client streams (SSE frame) and after a reload (rebuilt
  /// from the persisted `status{phase:reconnect}` block), so it no longer
  /// requires [streaming].
  bool get showReconnectIndicator => agentWorking && reconnectAttempt != null;

  /// UI-side cost estimate for usage metadata: runtime `cost_usd` wins, this
  /// only computes from the models catalog when the runtime did not report.
  double? usageCostUsd(String? model, int? inputTokens, int? outputTokens) {
    if (model == null || model.isEmpty) return null;
    if (inputTokens == null && outputTokens == null) return null;
    Map<String, dynamic>? entry;
    for (final m in availableModels) {
      final id = m['id'] as String?;
      final label = m['label'] as String?;
      if (id == model || label == model) {
        entry = m;
        break;
      }
    }
    if (entry == null) return null;
    double? price(Object? raw) {
      if (raw is num) return raw.toDouble();
      return num.tryParse('$raw')?.toDouble();
    }

    final inPrice = price(entry['input_price_usd_per_mtok']);
    final outPrice = price(entry['output_price_usd_per_mtok']);
    if (inPrice == null && outPrice == null) return null;
    var cost = 0.0;
    if (inPrice != null && inputTokens != null)
      cost += (inputTokens / 1e6) * inPrice;
    if (outPrice != null && outputTokens != null)
      cost += (outputTokens / 1e6) * outPrice;
    return cost;
  }

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

  void _invalidateVisible() {
    _visibleCache = null;
  }

  void _bumpStructure() {
    _structureRevision++;
    _contentRevision++;
    _invalidateVisible();
  }

  void _bumpContent() {
    _contentRevision++;
    _invalidateVisible();
  }

  void _rebuildBlockKeySet() {
    _blockKeys
      ..clear()
      ..addAll(blocks.map((b) => b.key));
  }

  /// Drop blocks already present in [blocks] (same widget key) and blocks that
  /// overlap the page boundary by content.
  ///
  /// Overlap guards pagination (older page) and live tailing (newer page).
  /// Content is only compared against a bounded window — the oldest blocks for
  /// a prepend, the newest for a poll — so identical historic messages are
  /// never accidentally swallowed.
  List<ChatBlock> _dropOverlap(
    List<ChatBlock> incoming, {
    required bool atHead,
    int window = 40,
  }) {
    final counts = <String, int>{};
    if (blocks.isNotEmpty) {
      final start = atHead
          ? 0
          : (blocks.length > window ? blocks.length - window : 0);
      final end = atHead
          ? (blocks.length < window ? blocks.length : window)
          : blocks.length;
      for (var i = start; i < end; i++) {
        final key = chatBlockContentKey(blocks[i]);
        counts[key] = (counts[key] ?? 0) + 1;
      }
    }
    final out = <ChatBlock>[];
    for (final block in incoming) {
      if (_blockKeys.contains(block.key)) continue;
      final key = chatBlockContentKey(block);
      final remaining = counts[key];
      if (remaining != null && remaining > 0) {
        counts[key] = remaining - 1;
        continue;
      }
      out.add(block);
    }
    return out;
  }

  /// Queue a streamed text/thinking chunk (already incremental) and flush it
  /// on the next frame budget tick instead of rebuilding per token.
  ///
  /// Chunks are grouped into ordered runs: consecutive deltas of the same kind
  /// are concatenated, while a text→thinking→text sequence keeps its three
  /// segments (the projection closes/opens blocks on those boundaries).
  void _queueDelta(String chunk, {required bool thinking}) {
    if (chunk.isEmpty) return;
    if (_pendingDeltas.isEmpty || _pendingDeltas.last.thinking != thinking) {
      _pendingDeltas.add(_PendingDelta(thinking: thinking));
    }
    _pendingDeltas.last.buffer.write(chunk);
    _deltaFlushTimer ??= Timer(_deltaFlushInterval, _flushPendingDeltas);
  }

  void _flushPendingDeltas() {
    _deltaFlushTimer?.cancel();
    _deltaFlushTimer = null;
    if (_pendingDeltas.isEmpty) return;
    final pending = List<_PendingDelta>.from(_pendingDeltas);
    _pendingDeltas.clear();
    final lengthBefore = _liveTurnBlocks.length;
    final hadEmptyAssistant = _liveTurnBlocks.any(
      (b) => b.kind == 'assistant_markdown' && b.text.trim().isEmpty,
    );
    for (final delta in pending) {
      final chunk = delta.buffer.toString();
      if (chunk.isEmpty) continue;
      _turnFirstOutputAt ??= DateTime.now().toUtc();
      _liveTurnBlocks = applyStreamEvent(_liveTurnBlocks, {
        'type': delta.thinking ? 'thinking_delta' : 'text_delta',
        'data': {'text': chunk},
      });
    }
    // Grouping only changes when the shape changes: a new block appeared, or
    // the live answer went from empty to non-empty (that is the moment it
    // becomes the visible «final answer»). Plain text growth is a content
    // change — the timeline and its cache stay valid.
    final becameNonEmpty =
        hadEmptyAssistant &&
        _liveTurnBlocks.any(
          (b) => b.kind == 'assistant_markdown' && b.text.trim().isNotEmpty,
        );
    if (_liveTurnBlocks.length != lengthBefore || becameNonEmpty) {
      _bumpStructure();
    } else {
      _bumpContent();
    }
    notify();
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
    _rebuildBlockKeySet();
    _bumpStructure();
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
      availableModels = raw is List
          ? raw.cast<Map<String, dynamic>>()
          : const [];
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
    // Best-effort: MCP display aliases — failures leave the chat on raw wire
    // names and never block the model list.
    try {
      final aliasBody = await api.getProjectMcpAliases(projectId);
      final raw = aliasBody['aliases'];
      mcpAliases = raw is Map
          ? raw.map((k, v) => MapEntry(k.toString(), v.toString()))
          : const <String, String>{};
      notify();
    } catch (_) {}
  }

  Future<void> loadTranscript({
    int? beforeSeq,
    bool background = false,
    bool silent = false,
  }) async {
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
      final parsed = chatBlocksFromTranscript(body['blocks'] as List?);
      if (beforeSeq == null) {
        blocks
          ..clear()
          ..addAll(parsed);
        _liveTurnBlocks = const [];
        _rebuildBlockKeySet();
        final pending = body['pending_approvals'];
        pendingApprovals = pending is List
            ? pending.cast<Map<String, dynamic>>()
            : const [];
        turnInProgress = body['turn_in_progress'] == true;
      } else {
        // Pagination may legitimately return zero new blocks near the head.
        // A broken/overlapping page must not duplicate blocks: match the
        // incoming page against the current *head* by content, and drop any
        // key that already exists (keys are page-scoped, so id-less blocks
        // never collide, but explicit wire ids can repeat).
        final fresh = _dropOverlap(parsed, atHead: true);
        blocks.insertAll(0, fresh);
        _blockKeys.addAll(fresh.map((b) => b.key));
      }
      _bumpStructure();
      hasMoreHistory = body['has_more'] == true;
      oldestSeq = body['oldest_seq'] as int?;
      // Only a full tail load defines the live cursor. An older page's
      // `newest_seq` belongs to that page — adopting it would rewind the
      // poller and re-fetch events the client already has.
      if (beforeSeq == null) {
        newestSeq = body['newest_seq'] as int?;
      }
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
        _syncReconnectFromBlocks();
        _syncLivePolling();
      }
      notifyImmediate();
    } catch (e) {
      // Best-effort transcript load: do not crash the chat. Keep the cached /
      // existing blocks visible; surface the error so the UI can show a
      // themed snackbar without leaving the composer in a dead state.
      // `silent` (post-cancel refresh) never overwrites the turn's own state.
      if (!silent) error = e;
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
    final beforeBlocks = blocks.length;
    final beforeOldest = oldestSeq;
    try {
      await loadTranscript(beforeSeq: oldestSeq);
      // Защита от бесконечной подгрузки. Контракт бэкенда: страница строго
      // старше before_seq, поэтому либо добавляются блоки, либо курсор
      // двигается. Если не произошло ни того, ни другого — дальше листать
      // нечего, иначе клиент будет запрашивать одну и ту же страницу вечно
      // (это и вешало чат: «открыл — через пару секунд страница замерла»).
      final noNewBlocks = blocks.length == beforeBlocks;
      final cursorStuck = oldestSeq != null && oldestSeq == beforeOldest;
      if (noNewBlocks && cursorStuck) {
        hasMoreHistory = false;
        oldestSeq = null;
      }
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

  Future<void> send(
    String text, {
    List<String> attachmentRefs = const [],
  }) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty && attachmentRefs.isEmpty) return;
    error = null;
    streaming = true;
    // This client now owns the live stream — stop any reload-tail polling.
    turnInProgress = false;
    _stopLivePolling();

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

    _turnFirstOutputAt = null;
    _turnEndedAt = null;
    final userBlock = createLiveBlock('user', {
      'text': trimmed,
      if (attachmentRefs.isNotEmpty) 'attachment_refs': attachmentRefs,
      // Optimistic send time (UTC ISO — same shape as the server format);
      // history reload replaces it with the persisted event timestamp.
      'created_at': DateTime.now().toUtc().toIso8601String(),
    });
    _liveTurnBlocks = [userBlock];
    _bumpStructure();
    notifyImmediate();

    reconnectAttempt = null;
    reconnectMaxAttempts = null;
    reconnectNextModel = null;
    _handle?.abort();
    final handle = api.projectChatStream(
      projectId: projectId,
      text: trimmed,
      sessionId: sessionId,
      model: selectedModel,
      attachmentRefs: attachmentRefs,
      turnId: _newTurnId(),
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
          _flushPendingDeltas();
          reconnectAttempt = null;
          final next = data['session_id'] as String?;
          if (next != null && next.isNotEmpty) {
            sessionId = next;
            onSessionCreated?.call(next);
          }
          final pending = data['pending_approvals'];
          if (pending is List)
            pendingApprovals = pending.cast<Map<String, dynamic>>();
          _turnEndedAt ??= DateTime.now().toUtc();
          _liveTurnBlocks = _attachTurnTimestamps(
            attachUsageToAssistant(finalizeTurnBlocks(_liveTurnBlocks)),
          );
          _bumpStructure();
        } else if (type == '_error' && data is Map<String, dynamic>) {
          _flushPendingDeltas();
          reconnectAttempt = null;
          error = ProdavanApiException(
            data['status'] is int ? data['status'] as int : 503,
            jsonEncode({
              'code': data['code'],
              'title': data['title'],
              'detail': data['detail'],
            }),
          );
        } else if (type == 'error') {
          _flushPendingDeltas();
          reconnectAttempt = null;
          if (data is Map<String, dynamic>) {
            error = AgentStreamError(data);
          } else if (data is Map) {
            error = AgentStreamError(Map<String, dynamic>.from(data));
          } else {
            error = AgentStreamError({'message': event.toString()});
          }
        } else if (type == 'done') {
          _flushPendingDeltas();
          reconnectAttempt = null;
          _turnEndedAt = DateTime.now().toUtc();
          _liveTurnBlocks = applyStreamEvent(_liveTurnBlocks, event);
          _bumpStructure();
          final doneData = data is Map<String, dynamic>
              ? data
              : (data is Map ? Map<String, dynamic>.from(data) : null);
          final reason = doneData?['reason']?.toString() ?? '';
          if (_turnFailureReasons.contains(reason) && error == null) {
            // The runtime ended the turn with a failure reason but no error
            // frame — synthesize one so the user sees what happened (inline
            // error block + error state) instead of a silently empty turn.
            final detail = doneData?['error_message']?.toString() ?? '';
            final errData = <String, dynamic>{
              'code': 'AGENT_TURN_FAILED',
              'message': detail.isNotEmpty
                  ? detail
                  : 'agent turn failed: $reason',
            };
            error = AgentStreamError(errData);
            _liveTurnBlocks = applyStreamEvent(_liveTurnBlocks, {
              'type': 'error',
              'data': errData,
            });
            _bumpStructure();
          }
          notify();
          continue;
        } else if (type == 'status' &&
            data is Map &&
            data['phase'] == 'reconnect') {
          // Provider-error reconnect (chat error policy): the runtime waits
          // the interval and re-invokes the model (optionally a fallback
          // model) — surface it as a status line instead of "working".
          reconnectAttempt = (data['attempt'] as num?)?.toInt();
          reconnectMaxAttempts = (data['max_attempts'] as num?)?.toInt();
          reconnectNextModel = data['next_model'] as String?;
          notifyImmediate();
        } else if (type == 'text_delta' || type == 'thinking_delta') {
          // Coalesce token deltas: applying one block copy + string concat per
          // token is quadratic in answer length and was the main reason a long
          // answer froze the UI. Buffers flush at ~25 fps; structural events
          // flush them immediately so ordering is preserved.
          final chunk = (data is Map ? data['text'] : null) as String? ?? '';
          if (chunk.isEmpty) continue;
          // Первый вывод после reconnect — подтверждаем немедленно (retry сработал).
          final firstAfterReconnect =
              reconnectAttempt != null || reconnectNextModel != null;
          reconnectAttempt = null;
          reconnectNextModel = null;
          _queueDelta(chunk, thinking: type == 'thinking_delta');
          if (firstAfterReconnect) {
            _flushPendingDeltas();
            notifyImmediate();
          }
          continue;
        } else {
          // Any structural event (tool call/result, thinking complete, usage,
          // approval, subagent…) drains buffered text first so the block order
          // matches the wire.
          _flushPendingDeltas();
          _liveTurnBlocks = applyStreamEvent(_liveTurnBlocks, event);
          _bumpStructure();
          notify();
          continue;
        }
        notify();
      }
      _flushPendingDeltas();
      if (watchdogFired) {
        // The turn never produced (or stopped producing) anything — mark it
        // interrupted and hand the user text back to the composer.
        error = _bridgeTimeoutError();
        _finalizeInterruptedTurn(trimmed);
      } else {
        final finalized = _attachTurnTimestamps(
          attachUsageToAssistant(_liveTurnBlocks),
        );
        blocks.addAll(finalized);
        _blockKeys.addAll(finalized.map((b) => b.key));
        _liveTurnBlocks = const [];
        _bumpStructure();
        if (pendingApprovals.isEmpty) {
          pendingApprovals = await _fetchPending();
        }
        _saveToCache();
      }
    } on ProdavanApiException catch (e) {
      _flushPendingDeltas();
      error = watchdogFired ? _bridgeTimeoutError() : e;
      _finalizeInterruptedTurn(trimmed);
      notifyImmediate();
    } catch (e) {
      _flushPendingDeltas();
      error = watchdogFired ? _bridgeTimeoutError() : e;
      _finalizeInterruptedTurn(trimmed);
      notifyImmediate();
    } finally {
      watchdog?.cancel();
      _flushPendingDeltas();
      streaming = false;
      reconnectAttempt = null;
      reconnectMaxAttempts = null;
      reconnectNextModel = null;
      // If the turn was cut short (watchdog / connection drop) the server may
      // still be working — resume tailing so the client keeps up.
      if (turnInProgress) _syncLivePolling();
      notifyImmediate();
    }
  }

  /// Stamp the turn's assistant message with its completion time and
  /// duration (first assistant output → end):
  /// - `created_at` — wall-clock completion moment (UTC ISO, server format);
  /// - `turn_ms` — only when the turn actually produced assistant output.
  ///
  /// History blocks carry the same fields derived server-side, so live and
  /// reloaded transcripts render identically. Turns with no assistant text
  /// (error / cancelled before any output) get nothing — there is no answer
  /// to date. The main (last non-empty) assistant block of the turn is the
  /// message the metadata row lives on.
  List<ChatBlock> _attachTurnTimestamps(List<ChatBlock> turnBlocks) {
    var target = -1;
    for (var i = turnBlocks.length - 1; i >= 0; i--) {
      if (turnBlocks[i].kind == 'assistant_markdown' &&
          turnBlocks[i].text.isNotEmpty) {
        target = i;
        break;
      }
    }
    if (target < 0) return turnBlocks;
    final endedAt = _turnEndedAt ?? DateTime.now().toUtc();
    final patch = <String, dynamic>{'created_at': endedAt.toIso8601String()};
    final firstOutputAt = _turnFirstOutputAt;
    if (firstOutputAt != null) {
      patch['turn_ms'] = endedAt.difference(firstOutputAt).inMilliseconds;
    }
    final out = List<ChatBlock>.from(turnBlocks);
    out[target] = out[target].copyWithRaw(patch);
    return out;
  }

  /// Connection dropped mid-turn: keep the partial transcript (marked
  /// interrupted, the way [cancelStream] marks cancelled) so the optimistic
  /// user message is not lost, and hand the user text back to the composer
  /// as an editable draft.
  void _finalizeInterruptedTurn(String userText) {
    if (_liveTurnBlocks.isNotEmpty) {
      _liveTurnBlocks = _attachTurnTimestamps(
        attachUsageToAssistant(
          finalizeTurnBlocks(_liveTurnBlocks, interrupted: true),
        ),
      );
      _blockKeys.addAll(_liveTurnBlocks.map((b) => b.key));
      blocks.addAll(_liveTurnBlocks);
      _liveTurnBlocks = const [];
      _bumpStructure();
      _saveToCache();
    }
    if (userText.trim().isNotEmpty) {
      interruptedDraft.value = userText;
    }
  }

  /// History + the in-flight turn as one immutable snapshot.
  ///
  /// Cached between notifications: the list can hold thousands of blocks and
  /// the scaffold reads it several times per build (empty check, checklist,
  /// message list). Invalidated by [_bumpStructure]/[_bumpContent].
  List<ChatBlock> get visibleBlocks => _visibleCache ??=
      List<ChatBlock>.unmodifiable([...blocks, ..._liveTurnBlocks]);

  Future<void> cancelStream() async {
    _handle?.abort();
    _flushPendingDeltas();
    _liveTurnBlocks = _attachTurnTimestamps(
      attachUsageToAssistant(
        finalizeTurnBlocks(_liveTurnBlocks, cancelled: true),
      ),
    );
    _blockKeys.addAll(_liveTurnBlocks.map((b) => b.key));
    blocks.addAll(_liveTurnBlocks);
    _liveTurnBlocks = const [];
    _bumpStructure();
    streaming = false;
    turnInProgress = false;
    _stopLivePolling();
    // Notify BEFORE the network call: the stop button must respond even when
    // the cancel request hangs on a bad network (Wave 6).
    notifyImmediate();
    if (sessionId.isNotEmpty) {
      try {
        await api.cancelAgentSession(
          projectId: projectId,
          sessionId: sessionId,
        );
      } catch (e) {
        // Best-effort server-side cancel: the local turn is already marked
        // cancelled; surface the failure via [error] so the UI can snack it
        // instead of crashing on an unhandled async error.
        error = e;
      }
      // Pull the server's final state for the stopped turn (the run may have
      // persisted events before the stop took effect).
      try {
        await loadTranscript(background: true, silent: true);
      } catch (_) {
        // Best-effort; the local cancel state already stands.
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

  /// Start/stop the live-tail poller so it runs only while the agent is
  /// working and this client is not the SSE owner. Idempotent — safe to call
  /// from any state transition (transcript load, send start/finish, poll).
  void _syncLivePolling() {
    final shouldPoll = turnInProgress && !streaming && sessionId.isNotEmpty;
    if (shouldPoll) {
      _livePollTimer ??= Timer.periodic(
        _livePollInterval,
        (_) => _pollLiveOnce(),
      );
    } else {
      _stopLivePolling();
    }
  }

  void _stopLivePolling() {
    _livePollTimer?.cancel();
    _livePollTimer = null;
  }

  /// One incremental fetch of events newer than [newestSeq]; appends them so
  /// a reloaded/navigated-back client catches up on the in-progress turn.
  Future<void> _pollLiveOnce() async {
    if (_polling || streaming || sessionId.isEmpty) return;
    final cursor = newestSeq;
    if (cursor == null) return;
    _polling = true;
    try {
      final body = await api.projectChatTranscript(
        projectId: projectId,
        sessionId: sessionId,
        afterSeq: cursor,
      );
      _pollFailures = 0;
      final resolved = body['session_id'] as String?;
      if (resolved != null && resolved.isNotEmpty) sessionId = resolved;
      final blocksBefore = blocks.length;
      final revisionBefore = _structureRevision;
      final turnBefore = turnInProgress;
      final incoming = _dropOverlap(
        chatBlocksFromTranscript(body['blocks'] as List?),
        atHead: false,
      );
      _mergeIncrementalBlocks(incoming);
      newestSeq = body['newest_seq'] as int? ?? newestSeq;
      turnInProgress = body['turn_in_progress'] == true;
      // A terminal tail is authoritative: never keep polling a turn the server
      // no longer considers running (e.g. it finished between two polls).
      if (!turnInProgress) {
        _finalizePolledTail();
      }
      _syncReconnectFromBlocks();
      _syncLivePolling();
      // Не будим UI, если хвост не изменился: поллер тикает каждые 2.5 с и
      // раньше перестраивал экран даже на пустых ответах.
      if (blocks.length != blocksBefore ||
          _structureRevision != revisionBefore ||
          turnInProgress != turnBefore) {
        _saveToCache();
        notifyImmediate();
      }
    } catch (_) {
      // Best-effort tail: stop after repeated failures instead of hammering.
      _pollFailures++;
      if (_pollFailures >= _livePollMaxFailures) {
        turnInProgress = false;
        _stopLivePolling();
        notifyImmediate();
      }
    } finally {
      _polling = false;
    }
  }

  /// Close a tail block that the poll projection left looking "live" once the
  /// turn is gone. Server projections never mark `_streaming`, so this only
  /// touches a live-projected tail (e.g. a watchdog-aborted turn).
  void _finalizePolledTail() {
    var changed = false;
    for (var i = blocks.length - 1; i >= 0; i--) {
      final b = blocks[i];
      if (b.kind == 'user') break;
      if (b.isStreaming) {
        blocks[i] = b.copyWithRaw({'_streaming': false});
        changed = true;
      }
    }
    if (changed) _bumpStructure();
  }

  /// Rebuild the reconnect indicator from persisted blocks (reload path).
  ///
  /// Scans a bounded transcript tail for the newest decisive block: a
  /// `status{phase:reconnect}` means a retry is in flight; any assistant/tool
  /// output (or a non-reconnect status) means the retry resolved. This mirrors
  /// the live SSE `status` frame so a reloaded client shows «Попытка
  /// реконнекта…» instead of a misleading "agentscale работает…".
  void _syncReconnectFromBlocks() {
    final start = blocks.length > 120 ? blocks.length - 120 : 0;
    for (var i = blocks.length - 1; i >= start; i--) {
      final b = blocks[i];
      switch (b.kind) {
        case 'status':
          final phase = b.raw['phase']?.toString();
          if (phase == 'reconnect') {
            reconnectAttempt = _asInt(b.raw['attempt']);
            reconnectMaxAttempts = _asInt(b.raw['max_attempts']);
            reconnectNextModel = b.raw['next_model']?.toString();
          } else {
            reconnectAttempt = null;
            reconnectMaxAttempts = null;
            reconnectNextModel = null;
          }
          return;
        case 'assistant_markdown':
          if (b.text.trim().isNotEmpty) {
            reconnectAttempt = null;
            reconnectMaxAttempts = null;
            reconnectNextModel = null;
            return;
          }
          break;
        case 'tool_call':
        case 'tool_result':
        case 'error':
        case 'user':
          reconnectAttempt = null;
          reconnectMaxAttempts = null;
          reconnectNextModel = null;
          return;
        default:
          break;
      }
    }
  }

  static int? _asInt(Object? v) {
    if (v is int) return v;
    if (v is num) return v.toInt();
    return null;
  }

  /// Append incrementally-fetched blocks.
  ///
  /// A polled page is an independent projection, so an assistant/thinking run
  /// split by the cursor arrives as a second block: merge it into the existing
  /// tail instead of duplicating the message. [mergeStreamingText] also makes
  /// replaying the same chunk a no-op, so a duplicated poll frame can never
  /// grow the answer.
  void _mergeIncrementalBlocks(List<ChatBlock> incoming) {
    if (incoming.isEmpty) return;
    for (final b in incoming) {
      final tail = blocks.isEmpty ? null : blocks.last;
      final mergeable =
          tail != null &&
          ((b.kind == 'assistant_markdown' &&
                  tail.kind == 'assistant_markdown') ||
              (b.kind == 'thinking' && tail.kind == 'thinking'));
      if (mergeable) {
        final merged = mergeStreamingText(tail.text, b.text);
        if (merged == tail.text) continue;
        blocks[blocks.length - 1] = tail.copyWithRaw({
          'text': merged,
          if (b.raw['duration_ms'] != null) 'duration_ms': b.raw['duration_ms'],
        });
        continue;
      }
      blocks.add(b);
      _blockKeys.add(b.key);
    }
    if (blocks.isNotEmpty) {
      _bumpStructure();
    }
  }

  void dispose() {
    _handle?.abort();
    _stopLivePolling();
    _deltaFlushTimer?.cancel();
    _deltaFlushTimer = null;
    _notifyTimer?.cancel();
    _tick.close();
    interruptedDraft.dispose();
  }
}

/// One ordered run of buffered deltas (text or thinking), flushed as a unit.
class _PendingDelta {
  _PendingDelta({required this.thinking});

  final bool thinking;
  final StringBuffer buffer = StringBuffer();
}
