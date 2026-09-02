import 'dart:async';
import 'dart:convert';

import 'package:prodavan/core/api/agent_stream_error.dart';
import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';

/// Live SSE chat session — blocks projection with optimistic user + streaming assistant.
class ChatSessionController {
  ChatSessionController({required this.api, required this.projectId});

  final ProdavanApi api;
  final String projectId;

  String? sessionId;
  String? selectedModel;
  final List<ChatBlock> blocks = [];
  bool streaming = false;
  Object? error;
  List<Map<String, dynamic>> pendingApprovals = const [];
  List<Map<String, dynamic>> availableModels = const [];
  String? defaultModel;

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

  Future<void> loadTranscript() async {
    final previousSessionId = sessionId;
    final body = await api.projectChatTranscript(
      projectId: projectId,
      sessionId: sessionId,
    );
    final resolved = body['session_id'] as String?;
    sessionId = resolved ?? previousSessionId;
    blocks
      ..clear()
      ..addAll(chatBlocksFromTranscript(body['blocks'] as List?));
    _liveTurnBlocks = const [];
    pendingApprovals = await _fetchPending();
    notifyImmediate();
  }

  Future<List<Map<String, dynamic>>> _fetchPending() async {
    final sid = sessionId;
    if (sid == null) return const [];
    return api.listPendingApprovals(projectId: projectId, sessionId: sid);
  }

  Future<void> send(String text, {List<String> attachmentRefs = const []}) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty && attachmentRefs.isEmpty) return;
    error = null;
    streaming = true;

    final userBlock = ChatBlock(
      kind: 'user',
      raw: {
        'text': trimmed.isEmpty ? '(attachment)' : trimmed,
        if (attachmentRefs.isNotEmpty) 'attachment_refs': attachmentRefs,
      },
    );
    _liveTurnBlocks = [userBlock];
    notifyImmediate();

    _handle?.abort();
    _handle = api.projectChatStream(
      projectId: projectId,
      text: trimmed.isEmpty ? '(attachment)' : trimmed,
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
          if (next != null && next.isNotEmpty) sessionId = next;
        } else if (type == '_turn_complete' && data is Map<String, dynamic>) {
          final next = data['session_id'] as String?;
          if (next != null && next.isNotEmpty) sessionId = next;
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
    final sid = sessionId;
    if (sid != null) {
      await api.cancelAgentSession(projectId: projectId, sessionId: sid);
    }
    notifyImmediate();
  }

  Future<void> resolveApproval(String approvalId, String decision) async {
    final sid = sessionId;
    if (sid == null) return;
    await api.resolveToolApproval(
      projectId: projectId,
      sessionId: sid,
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
