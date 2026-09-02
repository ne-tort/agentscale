import 'package:prodavan/core/chat/models/chat_block.dart';

List<ChatBlock> chatBlocksFromTranscript(List<dynamic>? raw) {
  if (raw == null) return const [];
  return raw
      .whereType<Map>()
      .map((e) => ChatBlock.fromJson(Map<String, dynamic>.from(e)))
      .toList();
}

/// Cumulative SDK delta → incremental append (mirrors API [normalize_text_delta]).
({String incremental, String cumulative}) normalizeTextDelta(String previous, String chunk) {
  if (chunk.isEmpty) return (incremental: '', cumulative: previous);
  if (chunk.startsWith(previous)) {
    return (incremental: chunk.substring(previous.length), cumulative: chunk);
  }
  if (previous.startsWith(chunk)) {
    return (incremental: '', cumulative: previous);
  }
  return (incremental: chunk, cumulative: previous + chunk);
}

void _applyNormalizedDelta({
  required List<ChatBlock> next,
  required String kind,
  required String chunk,
  required String baseKey,
}) {
  final lastIdx = next.lastIndexWhere((b) => b.kind == kind && b.isStreaming);
  if (lastIdx >= 0) {
    final prev = next[lastIdx];
    final base = prev.raw[baseKey] as String? ?? prev.text;
    final normalized = normalizeTextDelta(base, chunk);
    if (normalized.incremental.isEmpty) return;
    next[lastIdx] = prev.copyWithRaw({
      'text': normalized.cumulative,
      baseKey: normalized.cumulative,
      '_streaming': true,
    });
    return;
  }
  final normalized = normalizeTextDelta('', chunk);
  next.add(ChatBlock(
    kind: kind,
    raw: {
      'text': normalized.cumulative,
      baseKey: normalized.cumulative,
      '_streaming': true,
    },
  ));
}

/// Apply a single SSE agent event to turn blocks (live streaming).
List<ChatBlock> applyStreamEvent(List<ChatBlock> blocks, Map<String, dynamic> event) {
  final type = event['type'] as String?;
  final data = event['data'];
  final payload = data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  if (type == null) return blocks;

  final next = List<ChatBlock>.from(blocks);

  switch (type) {
    case 'text_delta':
      final chunk = payload['text'] as String? ?? '';
      if (chunk.isEmpty) return next;
      _applyNormalizedDelta(
        next: next,
        kind: 'assistant_markdown',
        chunk: chunk,
        baseKey: '_textBase',
      );
      break;
    case 'thinking_delta':
      final chunk = payload['text'] as String? ?? '';
      if (chunk.isEmpty) break;
      _applyNormalizedDelta(
        next: next,
        kind: 'thinking',
        chunk: chunk,
        baseKey: '_thinkingBase',
      );
      break;
    case 'thinking_complete':
      final idx = next.lastIndexWhere((b) => b.kind == 'thinking');
      if (idx >= 0) {
        final prev = next[idx];
        next[idx] = prev.copyWithRaw({
          '_streaming': false,
          if (payload['duration_ms'] != null) 'duration_ms': payload['duration_ms'],
        });
      }
      break;
    case 'tool_call':
      next.add(ChatBlock(kind: 'tool_call', raw: {
        'id': payload['id'],
        'name': payload['name'],
        'input': payload['input'] ?? {},
      }));
      break;
    case 'tool_result':
      next.add(ChatBlock(kind: 'tool_result', raw: {
        'id': payload['id'],
        'name': payload['name'],
        'output': payload['output'],
        'is_error': payload['is_error'] == true,
      }));
      break;
    case 'tool_approval_request':
      next.add(ChatBlock(kind: 'approval', raw: {
        'id': payload['id'] ?? '',
        'name': payload['name'] ?? 'tool',
        'input': payload['input'] ?? {},
        'reason': payload['reason'],
      }));
      break;
    case 'subagent_start':
      next.add(ChatBlock(kind: 'subagent', raw: {
        'id': payload['agent_id'] ?? payload['parent_tool_use_id'] ?? '',
        'agent_id': payload['agent_id'],
        'agent_type': payload['type'] ?? payload['agent_type'],
        'parent_tool_use_id': payload['parent_tool_use_id'],
        'events': <dynamic>[],
      }));
      break;
    case 'subagent_event':
      final subId = payload['parent_tool_use_id'] as String? ?? '';
      final child = payload['child_event'] ?? payload['event'];
      final idx = next.indexWhere((b) => b.kind == 'subagent' && b.id == subId);
      if (idx >= 0 && child is Map) {
        final prev = next[idx];
        final events = List<dynamic>.from(prev.raw['events'] as List? ?? []);
        events.add(child);
        next[idx] = prev.copyWithRaw({'events': events});
      }
      break;
    case 'subagent_stop':
      final subId = payload['agent_id'] as String? ?? payload['parent_tool_use_id'] as String? ?? '';
      final idx = next.indexWhere((b) => b.kind == 'subagent' && b.id == subId);
      if (idx >= 0) {
        next[idx] = next[idx].copyWithRaw({
          'result_summary': payload['result_summary'],
        });
      }
      break;
    case 'task_progress':
      final tasks = payload['tasks'] ?? payload['items'] ?? [];
      if (tasks is List && tasks.isNotEmpty) {
        next.add(ChatBlock(kind: 'plan', raw: {
          'tasks': tasks,
          'message': payload['message'],
        }));
      }
      break;
    case 'usage':
      final usageIdx = next.lastIndexWhere((b) => b.kind == 'usage');
      final usageRaw = Map<String, dynamic>.from(payload);
      if (usageIdx >= 0) {
        next[usageIdx] = next[usageIdx].copyWithRaw(usageRaw);
      } else {
        next.add(ChatBlock(kind: 'usage', raw: usageRaw));
      }
      break;
    case 'status':
    case 'tool_progress':
    case 'tool_call_delta':
    case 'system_notice':
      break;
    case 'error':
      next.add(ChatBlock(kind: 'error', raw: {
        'code': payload['code'],
        'message': payload['message'] ?? 'Agent error',
        'retryable': payload['retryable'] == true,
      }));
      break;
    case 'done':
      for (var i = 0; i < next.length; i++) {
        if (next[i].isStreaming) {
          next[i] = next[i].copyWithRaw({'_streaming': false});
        }
      }
      break;
  }
  return next;
}

List<ChatBlock> finalizeTurnBlocks(List<ChatBlock> blocks, {bool cancelled = false}) {
  return blocks.map((b) {
    if (b.isStreaming) {
      return b.copyWithRaw({'_streaming': false, if (cancelled) '_cancelled': true});
    }
    if (cancelled && b.kind == 'assistant_markdown') {
      return b.copyWithRaw({'_cancelled': true});
    }
    return b;
  }).toList();
}
