import 'package:prodavan/core/chat/models/chat_block.dart';

/// Monotonic generator for live block identities (`raw['_key']`).
///
/// Reverse-list keying needs an identity that never changes while a block
/// streams and never collides — wire ids are missing on most blocks and
/// text-prefix hashes are unstable, so every block born in the live
/// projection gets a sequential key instead.
class _KeyGen {
  _KeyGen._();

  static int _seq = 0;

  static String next(String kind) => '$kind#${++_seq}';
}

List<ChatBlock> chatBlocksFromTranscript(List<dynamic>? raw) {
  if (raw == null) return const [];
  final out = <ChatBlock>[];
  final usedKeys = <String>{};
  var i = 0;
  for (final e in raw) {
    final idx = i++;
    if (e is! Map) continue;
    final block = ChatBlock.fromJson(Map<String, dynamic>.from(e));
    // Explicit wire id when present, else positional — deduped defensively.
    var key = block.id.isNotEmpty ? block.id : 'h$idx';
    if (!usedKeys.add(key)) key = '$key#$idx';
    out.add(block.copyWithRaw({'_key': key}));
  }
  return attachUsageToAssistant(out);
}

/// Cumulative SDK delta → incremental append (API / transcript rebuild only).
///
/// Live SSE uses append-only ([_applyIncrementalDelta]). This helper mirrors
/// API [normalize_text_delta]: cumulative prefix OR verbatim append — **no**
/// suffix/prefix overlap merge (that swallowed syllables on incremental streams).
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

void _closeStreamingKind(List<ChatBlock> next, String kind) {
  for (var i = 0; i < next.length; i++) {
    if (next[i].kind == kind && next[i].isStreaming) {
      next[i] = next[i].copyWithRaw({'_streaming': false});
    }
  }
}

/// Append-only apply for wire-incremental deltas (Open WebUI / Ollama-style).
void _applyIncrementalDelta({
  required List<ChatBlock> next,
  required String kind,
  required String chunk,
  required String baseKey,
}) {
  final lastIdx = next.lastIndexWhere((b) => b.kind == kind && b.isStreaming);
  if (lastIdx >= 0) {
    final prev = next[lastIdx];
    final base = prev.raw[baseKey] as String? ?? prev.text;
    final cumulative = base + chunk;
    next[lastIdx] = prev.copyWithRaw({
      'text': cumulative,
      baseKey: cumulative,
      '_streaming': true,
    });
    return;
  }
  next.add(ChatBlock(
    kind: kind,
    raw: {
      'text': chunk,
      baseKey: chunk,
      '_streaming': true,
      '_key': _KeyGen.next(kind),
    },
  ));
}

String? _nonEmptyId(Object? value) => value is String && value.isNotEmpty ? value : null;

/// Subagent block ↔ event matching by any shared identifier.
///
/// The block id is `agent_id ?? parent_tool_use_id`, but events may carry
/// either (or both) — matching on a single field silently dropped events
/// whenever the two ids disagreed.
bool _subagentMatches(ChatBlock block, Map<String, dynamic> payload) {
  final blockIds = {
    _nonEmptyId(block.id),
    _nonEmptyId(block.raw['agent_id']),
    _nonEmptyId(block.raw['parent_tool_use_id']),
  }.whereType<String>().toSet();
  if (blockIds.isEmpty) return false;
  final payloadIds = {
    _nonEmptyId(payload['parent_tool_use_id']),
    _nonEmptyId(payload['agent_id']),
    _nonEmptyId(payload['id']),
  }.whereType<String>().toSet();
  return payloadIds.any(blockIds.contains);
}

const _usageSumIntFields = [
  'input_tokens',
  'output_tokens',
  'cache_creation_tokens',
  'cache_read_tokens',
];

/// Merge one usage payload into an accumulated usage map.
///
/// Token counters sum across the turn's LLM requests, cost sums when the
/// runtime reports it, model/provider take the latest non-null value.
Map<String, dynamic> mergeUsagePayload(Map<String, dynamic> current, Map<String, dynamic> payload) {
  final out = Map<String, dynamic>.from(current);
  for (final f in _usageSumIntFields) {
    final v = payload[f];
    if (v is num) {
      final prev = out[f];
      out[f] = (prev is num ? prev : 0).toInt() + v.toInt();
    }
  }
  final cost = payload['cost_usd'];
  if (cost is num) {
    final prev = out['cost_usd'];
    out['cost_usd'] = (prev is num ? prev : 0).toDouble() + cost.toDouble();
  }
  for (final f in const ['model', 'provider']) {
    final v = payload[f];
    if (v != null) out[f] = v;
  }
  return out;
}

void _attachUsageInPlace(List<ChatBlock> next, Map<String, dynamic> usageRaw) {
  var target = -1;
  for (var i = next.length - 1; i >= 0; i--) {
    if (next[i].kind == 'assistant_markdown') {
      target = i;
      break;
    }
  }
  if (target < 0) return; // no home — data persists server-side
  final prev = next[target];
  final current = prev.raw['usage'];
  next[target] = prev.copyWithRaw({
    'usage': mergeUsagePayload(
      current is Map ? Map<String, dynamic>.from(current) : const {},
      usageRaw,
    ),
  });
}

/// Create a live block with an assigned stable `_key` (used by the controller
/// for the optimistic user message before any SSE event arrives).
ChatBlock createLiveBlock(String kind, Map<String, dynamic> raw) {
  return ChatBlock(kind: kind, raw: {...raw, '_key': _KeyGen.next(kind)});
}

/// Attach standalone `usage` blocks to their nearest preceding assistant
/// message (accumulating) and remove them from the list. Usage without an
/// assistant home is dropped — the authoritative numbers live server-side.
List<ChatBlock> attachUsageToAssistant(List<ChatBlock> blocks) {
  if (!blocks.any((b) => b.kind == 'usage')) return blocks;
  final out = <ChatBlock>[];
  for (final b in blocks) {
    if (b.kind != 'usage') {
      out.add(b);
      continue;
    }
    _attachUsageInPlace(out, b.raw);
  }
  return out;
}

/// Apply a single SSE agent event to turn blocks (live streaming).
List<ChatBlock> applyStreamEvent(List<ChatBlock> blocks, Map<String, dynamic> event) {
  final type = event['type'] as String?;
  final data = event['data'];
  final payload = data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  if (type == null) return blocks;

  final next = List<ChatBlock>.from(blocks);

  switch (type) {
    case 'user_message':
      // Повтор run-stall сторожа (stall_retry) — не второй пузырь пользователя:
      // ретрай обслуживается reconnect-системой и в транскрипте невидим.
      if (payload['stall_retry'] == true) break;
      final userRaw = Map<String, dynamic>.from(payload);
      final idx = next.indexWhere((b) => b.kind == 'user');
      if (idx >= 0) {
        // Server echo of the optimistic block — keep local widget identity
        // and the optimistic send time (the echo payload carries no ts).
        userRaw['_key'] = next[idx].key;
        userRaw['created_at'] ??= next[idx].raw['created_at'];
        next[idx] = ChatBlock(kind: 'user', raw: userRaw);
      } else {
        userRaw['_key'] = _KeyGen.next('user');
        next.insert(0, ChatBlock(kind: 'user', raw: userRaw));
      }
      break;
    case 'text_delta':
      final chunk = payload['text'] as String? ?? '';
      if (chunk.isEmpty) return next;
      _applyIncrementalDelta(
        next: next,
        kind: 'assistant_markdown',
        chunk: chunk,
        baseKey: '_textBase',
      );
      break;
    case 'thinking_delta':
      // Mirror API TurnStreamNormalizer: thinking starts a new text segment after.
      _closeStreamingKind(next, 'assistant_markdown');
      final chunk = payload['text'] as String? ?? '';
      if (chunk.isEmpty) break;
      _applyIncrementalDelta(
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
      // Close streaming assistant so post-tool text starts a new block (no glue).
      _closeStreamingKind(next, 'assistant_markdown');
      _closeStreamingKind(next, 'thinking');
      next.add(ChatBlock(kind: 'tool_call', raw: {
        'id': payload['id'],
        'name': payload['name'],
        'input': payload['input'] ?? {},
        '_key': _KeyGen.next('tool_call'),
      }));
      break;
    case 'tool_result':
      next.add(ChatBlock(kind: 'tool_result', raw: {
        'id': payload['id'],
        'name': payload['name'],
        'output': payload['output'],
        'is_error': payload['is_error'] == true,
        '_key': _KeyGen.next('tool_result'),
      }));
      break;
    case 'tool_approval_request':
      next.add(ChatBlock(kind: 'approval', raw: {
        'id': payload['id'] ?? '',
        'name': payload['name'] ?? 'tool',
        'input': payload['input'] ?? {},
        'reason': payload['reason'],
        '_key': _KeyGen.next('approval'),
      }));
      break;
    case 'subagent_start':
      next.add(ChatBlock(kind: 'subagent', raw: {
        'id': payload['agent_id'] ?? payload['parent_tool_use_id'] ?? '',
        'agent_id': payload['agent_id'],
        'agent_type': payload['type'] ?? payload['agent_type'],
        'parent_tool_use_id': payload['parent_tool_use_id'],
        'events': <dynamic>[],
        '_key': _KeyGen.next('subagent'),
      }));
      break;
    case 'subagent_event':
      final child = payload['child_event'] ?? payload['event'];
      final idx = next.indexWhere((b) => b.kind == 'subagent' && _subagentMatches(b, payload));
      if (idx >= 0 && child is Map) {
        final prev = next[idx];
        final events = List<dynamic>.from(prev.raw['events'] as List? ?? []);
        events.add(child);
        next[idx] = prev.copyWithRaw({'events': events});
      }
      break;
    case 'subagent_stop':
      final idx = next.indexWhere((b) => b.kind == 'subagent' && _subagentMatches(b, payload));
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
          '_key': _KeyGen.next('plan'),
        }));
      }
      break;
    case 'usage':
      // No standalone usage block: attach to the nearest preceding assistant
      // message immediately so live hover metadata stays in sync.
      _attachUsageInPlace(next, Map<String, dynamic>.from(payload));
      break;
    case 'permission_denial':
      next.add(ChatBlock(kind: 'permission_denial', raw: {
        'name': payload['name'] ?? payload['tool'],
        'reason': payload['reason'],
        '_key': _KeyGen.next('permission_denial'),
      }));
      break;
    case 'status':
    case 'tool_progress':
    case 'tool_call_delta':
      break;
    case 'error':
      // Легаси-маркер старой версии run-stall сторожа: stall обрабатывается
      // reconnect-системой (status-кадры), такие error-события не рендерим.
      if (payload['code'] == 'CHAT_RUN_STALLED') break;
      next.add(ChatBlock(kind: 'error', raw: {
        'code': payload['code'],
        'message': payload['message'] ?? 'Agent error',
        'retryable': payload['retryable'] == true,
        '_key': _KeyGen.next('error'),
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

/// Finalize a turn: close streaming flags and optionally mark the partial
/// assistant reply — [cancelled] for user cancels, [interrupted] for
/// connection drops (SSE break) — so the transcript keeps an honest tail.
List<ChatBlock> finalizeTurnBlocks(
  List<ChatBlock> blocks, {
  bool cancelled = false,
  bool interrupted = false,
}) {
  final marker = cancelled
      ? '_cancelled'
      : interrupted
          ? '_interrupted'
          : null;
  return blocks.map((b) {
    if (b.isStreaming) {
      return b.copyWithRaw({
        '_streaming': false,
        ?marker: true,
      });
    }
    if (marker != null && b.kind == 'assistant_markdown') {
      return b.copyWithRaw({marker: true});
    }
    return b;
  }).toList();
}
