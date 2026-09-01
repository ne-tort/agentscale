import 'dart:async';
import 'dart:convert';

import 'package:prodavan/core/api/agent_stream_error.dart';
import 'package:prodavan/core/api/prodavan_api.dart';

/// SSE chat state for [ProjectWorkspacePage].
class ProjectChatController {
  ProjectChatController({required this.api, required this.projectId});

  final ProdavanApi api;
  final String projectId;

  String? sessionId;
  final List<Map<String, dynamic>> messages = [];
  bool streaming = false;
  Object? error;
  List<Map<String, dynamic>> pendingApprovals = const [];

  ProjectChatStreamHandle? _handle;
  final _tick = StreamController<void>.broadcast();

  Stream<void> get changes => _tick.stream;

  void notify() {
    if (!_tick.isClosed) _tick.add(null);
  }

  Future<void> loadTranscript() async {
    final body = await api.projectChatTranscript(
      projectId: projectId,
      sessionId: sessionId,
    );
    sessionId = body['session_id'] as String?;
    final raw = body['messages'];
    messages
      ..clear()
      ..addAll(raw is List ? raw.cast<Map<String, dynamic>>() : const []);
    pendingApprovals = await _fetchPending();
    notify();
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
    notify();

    _handle?.abort();
    _handle = api.projectChatStream(
      projectId: projectId,
      text: trimmed.isEmpty ? '(attachment)' : trimmed,
      sessionId: sessionId,
      attachmentRefs: attachmentRefs,
    );

    try {
      await for (final event in _handle!.stream) {
        final type = event['type'] as String?;
        final data = event['data'];
        if (type == '_session' && data is Map<String, dynamic>) {
          sessionId = data['session_id'] as String? ?? sessionId;
        } else if (type == '_turn_complete' && data is Map<String, dynamic>) {
          sessionId = data['session_id'] as String? ?? sessionId;
          final pending = data['pending_approvals'];
          if (pending is List) {
            pendingApprovals = pending.cast<Map<String, dynamic>>();
          }
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
        }
        notify();
      }
      await loadTranscript();
    } on ProdavanApiException catch (e) {
      error = e;
      notify();
    } catch (e) {
      error = e;
      notify();
    } finally {
      streaming = false;
      notify();
    }
  }

  Future<void> cancelStream() async {
    _handle?.abort();
    streaming = false;
    final sid = sessionId;
    if (sid != null) {
      await api.cancelAgentSession(projectId: projectId, sessionId: sid);
    }
    notify();
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
    _tick.close();
  }
}
