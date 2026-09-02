import 'package:prodavan/core/chat/models/chat_block.dart';

List<ChatBlock> chatBlocksFromTranscript(List<dynamic>? raw) {
  if (raw == null) return const [];
  return raw
      .whereType<Map>()
      .map((e) => ChatBlock.fromJson(Map<String, dynamic>.from(e)))
      .toList();
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
      final lastIdx = next.lastIndexWhere((b) => b.kind == 'assistant_markdown' && b.isStreaming);
      if (lastIdx >= 0) {
        final prev = next[lastIdx];
        next[lastIdx] = prev.copyWithRaw({
          'text': '${prev.text}$chunk',
          '_streaming': true,
        });
      } else {
        next.add(ChatBlock(
          kind: 'assistant_markdown',
          raw: {'text': chunk, '_streaming': true},
        ));
      }
    case 'thinking_delta':
      final chunk = payload['text'] as String? ?? '';
      if (chunk.isEmpty) break;
      final idx = next.lastIndexWhere((b) => b.kind == 'thinking' && b.isStreaming);
      if (idx >= 0) {
        final prev = next[idx];
        next[idx] = prev.copyWithRaw({'text': '${prev.text}$chunk', '_streaming': true});
      } else {
        next.add(ChatBlock(kind: 'thinking', raw: {'text': chunk, '_streaming': true}));
      }
    case 'thinking_complete':
      final idx = next.lastIndexWhere((b) => b.kind == 'thinking');
      if (idx >= 0) {
        final prev = next[idx];
        next[idx] = prev.copyWithRaw({
          '_streaming': false,
          if (payload['duration_ms'] != null) 'duration_ms': payload['duration_ms'],
        });
      }
    case 'tool_call':
      next.add(ChatBlock(kind: 'tool_call', raw: {
        'id': payload['id'],
        'name': payload['name'],
        'input': payload['input'] ?? {},
        'status': 'running',
      }));
    case 'tool_result':
      next.add(ChatBlock(kind: 'tool_result', raw: {
        'id': payload['id'],
        'name': payload['name'],
        'output': payload['output'],
        'is_error': payload['is_error'] == true,
      }));
    case 'tool_approval_request':
      next.add(ChatBlock(kind: 'approval', raw: {
        'id': payload['id'] ?? '',
        'name': payload['name'] ?? 'tool',
        'input': payload['input'] ?? {},
        'reason': payload['reason'],
      }));
    case 'subagent_start':
      next.add(ChatBlock(kind: 'subagent', raw: {
        'id': payload['agent_id'] ?? payload['parent_tool_use_id'] ?? '',
        'agent_id': payload['agent_id'],
        'agent_type': payload['type'] ?? payload['agent_type'],
        'parent_tool_use_id': payload['parent_tool_use_id'],
        'status': 'running',
        'events': <dynamic>[],
      }));
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
    case 'subagent_stop':
      final subId = payload['agent_id'] as String? ?? payload['parent_tool_use_id'] as String? ?? '';
      final idx = next.indexWhere((b) => b.kind == 'subagent' && b.id == subId);
      if (idx >= 0) {
        next[idx] = next[idx].copyWithRaw({
          'status': 'completed',
          'result_summary': payload['result_summary'],
        });
      }
    case 'task_progress':
      next.add(ChatBlock(kind: 'plan', raw: {
        'tasks': payload['tasks'] ?? payload['items'] ?? [],
        'message': payload['message'],
      }));
    case 'error':
      next.add(ChatBlock(kind: 'error', raw: {
        'code': payload['code'],
        'message': payload['message'] ?? 'Agent error',
        'retryable': payload['retryable'] == true,
      }));
    case 'done':
      for (var i = 0; i < next.length; i++) {
        if (next[i].isStreaming) {
          next[i] = next[i].copyWithRaw({'_streaming': false});
        }
      }
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
