import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart' show ActivityGroupKind;

typedef ChatDisplayPair = ({ChatBlock block, ChatBlock? paired});

sealed class ChatDisplayEntry {
  const ChatDisplayEntry();
}

class ChatDisplaySingle extends ChatDisplayEntry {
  const ChatDisplaySingle({required this.item});

  final ChatDisplayPair item;
}

class ChatDisplayGroup extends ChatDisplayEntry {
  const ChatDisplayGroup({required this.kind, required this.items});

  final ActivityGroupKind kind;
  final List<ChatDisplayPair> items;
}

class ChatDisplayWorkSession extends ChatDisplayEntry {
  const ChatDisplayWorkSession({
    required this.items,
    required this.streaming,
  });

  final List<ChatDisplayPair> items;
  final bool streaming;
}

ActivityGroupKind? activityGroupKind(ChatDisplayPair item) {
  final block = item.block;
  if (block.kind == 'thinking') return ActivityGroupKind.thinking;
  if (block.kind != 'tool_call' && block.kind != 'tool_result') return null;

  return switch (toolKindFromPair(_toolName(block, item.paired), _toolInput(block, item.paired))) {
    ToolKind.fileRead => ActivityGroupKind.fileRead,
    ToolKind.fileWrite || ToolKind.fileEdit => ActivityGroupKind.fileEdit,
    ToolKind.fileDelete => ActivityGroupKind.fileDelete,
    ToolKind.searchGlob => ActivityGroupKind.searchGlob,
    ToolKind.searchGrep => ActivityGroupKind.searchGrep,
    ToolKind.listDir => ActivityGroupKind.listDir,
    ToolKind.shell => ActivityGroupKind.command,
    ToolKind.mcp => ActivityGroupKind.mcp,
    ToolKind.subagent || ToolKind.generic => ActivityGroupKind.generic,
  };
}

String _toolName(ChatBlock block, ChatBlock? paired) {
  return block.raw['name'] as String? ?? paired?.raw['name'] as String? ?? 'tool';
}

Map<String, dynamic> _toolInput(ChatBlock block, ChatBlock? paired) {
  final raw = block.raw['input'] ?? paired?.raw['input'];
  if (raw is Map) return Map<String, dynamic>.from(raw);
  return const {};
}

bool _isActivityPair(ChatDisplayPair item) {
  final k = item.block.kind;
  return k == 'tool_call' || k == 'tool_result';
}

bool _isThinkingBlock(ChatBlock block) => block.kind == 'thinking';

bool _isWorkSegmentItem(ChatDisplayPair item) =>
    _isThinkingBlock(item.block) || _isActivityPair(item);

/// Streaming / empty assistant must not split a WorkSession mid-turn.
bool _isSoftPassthrough(ChatBlock block) {
  if (block.kind != 'assistant_markdown') return false;
  return block.isStreaming || block.text.trim().isEmpty;
}

bool _continuesWorkRun(ChatDisplayPair item) =>
    _isWorkSegmentItem(item) || _isSoftPassthrough(item.block);

bool _isHardBoundary(ChatBlock block) {
  return switch (block.kind) {
    'user' || 'approval' || 'plan' || 'usage' || 'error' || 'subagent' => true,
    'assistant_markdown' => block.text.trim().isNotEmpty && !block.isStreaming,
    _ => false,
  };
}

bool _pairIsPending(ChatDisplayPair item) {
  if (item.block.kind == 'tool_call' && item.paired == null) return true;
  return false;
}

bool _thinkingIsStreaming(ChatDisplayPair item) =>
    item.block.kind == 'thinking' && item.block.isStreaming;

bool _workSessionStreaming(List<ChatDisplayPair> items, bool turnStreaming) {
  if (!turnStreaming) return false;
  return items.any(_pairIsPending) || items.any(_thinkingIsStreaming);
}

int? _findToolResultIndex(List<ChatBlock> blocks, int callIndex, Set<int> usedResults) {
  final call = blocks[callIndex];
  if (call.kind != 'tool_call') return null;
  final callId = call.raw['id'];

  if (callIndex + 1 < blocks.length && !usedResults.contains(callIndex + 1)) {
    final next = blocks[callIndex + 1];
    if (next.kind == 'tool_result') {
      final resultId = next.raw['id'];
      if (callId != null && resultId != null && callId == resultId) return callIndex + 1;
      if (callId == null && resultId == null) return callIndex + 1;
    }
  }

  if (callId == null) return null;
  final limit = blocks.length < callIndex + 25 ? blocks.length : callIndex + 25;
  for (var j = callIndex + 1; j < limit; j++) {
    if (usedResults.contains(j)) continue;
    final b = blocks[j];
    if (b.kind == 'tool_result' && b.raw['id'] == callId) return j;
  }
  return null;
}

List<ChatDisplayPair> mergeToolPairs(List<ChatBlock> blocks) {
  final usedResults = <int>{};
  final out = <ChatDisplayPair>[];
  for (var i = 0; i < blocks.length; i++) {
    if (usedResults.contains(i)) continue;
    final block = blocks[i];
    if (block.kind == 'tool_call') {
      final j = _findToolResultIndex(blocks, i, usedResults);
      if (j != null) {
        usedResults.add(j);
        out.add((block: block, paired: blocks[j]));
        continue;
      }
    }
    out.add((block: block, paired: null));
  }
  return out;
}

/// Merge consecutive thinking blocks into one spoiler (single text + duration).
ChatDisplayPair mergeThinkingPairs(List<ChatDisplayPair> items) {
  if (items.isEmpty) {
    return (block: ChatBlock(kind: 'thinking', raw: const {'text': ''}), paired: null);
  }
  if (items.length == 1) return items.first;

  final buf = StringBuffer();
  var duration = 0;
  var streaming = false;
  for (final item in items) {
    final t = item.block.text.trim();
    if (t.isNotEmpty) {
      if (buf.isNotEmpty) buf.writeln();
      buf.write(t);
    }
    final d = item.block.raw['duration_ms'];
    if (d is int && d > duration) duration = d;
    if (item.block.isStreaming) streaming = true;
  }
  return (
    block: ChatBlock(
      kind: 'thinking',
      raw: {
        'text': buf.toString(),
        if (duration > 0) 'duration_ms': duration,
        if (streaming) '_streaming': true,
      },
    ),
    paired: null,
  );
}

List<ChatDisplayEntry> _groupThinkingRun(List<ChatDisplayPair> merged, int start, int end) {
  final slice = merged.sublist(start, end);
  return [ChatDisplaySingle(item: mergeThinkingPairs(slice))];
}

List<ChatDisplayEntry> _groupSameKindRun(List<ChatDisplayPair> items) {
  if (items.length < 2) return [ChatDisplaySingle(item: items.first)];
  final kind = activityGroupKind(items.first);
  if (kind == null || kind == ActivityGroupKind.thinking) {
    return [ChatDisplaySingle(item: items.first)];
  }
  var same = true;
  for (final item in items.skip(1)) {
    if (activityGroupKind(item) != kind) {
      same = false;
      break;
    }
  }
  if (same) {
    return [ChatDisplayGroup(kind: kind, items: items)];
  }
  return items.map((i) => ChatDisplaySingle(item: i)).toList();
}

List<ChatDisplayEntry> _emitWorkSegmentRun(List<ChatDisplayPair> run, bool turnStreaming) {
  final workItems = run.where(_isWorkSegmentItem).toList();
  if (workItems.length >= 2) {
    return [
      ChatDisplayWorkSession(
        items: run,
        streaming: _workSessionStreaming(workItems, turnStreaming),
      ),
    ];
  }
  if (run.isEmpty) return const [];
  if (run.length == 1) return [ChatDisplaySingle(item: run.first)];
  return run.map((i) => ChatDisplaySingle(item: i)).toList();
}

List<ChatDisplayEntry> groupDisplayEntries(List<ChatBlock> blocks, {bool turnStreaming = false}) {
  final merged = mergeToolPairs(blocks);
  final out = <ChatDisplayEntry>[];
  var i = 0;
  while (i < merged.length) {
    final block = merged[i].block;

    if (_isHardBoundary(block)) {
      out.add(ChatDisplaySingle(item: merged[i]));
      i++;
      continue;
    }

    if (_continuesWorkRun(merged[i])) {
      var j = i;
      while (j < merged.length && _continuesWorkRun(merged[j])) {
        j++;
      }
      out.addAll(_emitWorkSegmentRun(merged.sublist(i, j), turnStreaming));
      i = j;
      continue;
    }

    out.add(ChatDisplaySingle(item: merged[i]));
    i++;
  }
  return out;
}

/// Inner grouping for expanded work session (same-kind sub-headers).
List<ChatDisplayEntry> groupInnerWorkItems(List<ChatDisplayPair> items) {
  final out = <ChatDisplayEntry>[];
  var i = 0;
  while (i < items.length) {
    final kind = activityGroupKind(items[i]);
    if (kind == ActivityGroupKind.thinking) {
      var j = i + 1;
      while (j < items.length && activityGroupKind(items[j]) == ActivityGroupKind.thinking) {
        j++;
      }
      out.addAll(_groupThinkingRun(items, i, j));
      i = j;
      continue;
    }
    if (kind == null) {
      out.add(ChatDisplaySingle(item: items[i]));
      i++;
      continue;
    }
    var j = i + 1;
    while (j < items.length && activityGroupKind(items[j]) == kind) {
      j++;
    }
    out.addAll(_groupSameKindRun(items.sublist(i, j)));
    i = j;
  }
  return out;
}

({int added, int removed})? aggregateDiffStats(List<ChatDisplayPair> items) {
  var added = 0;
  var removed = 0;
  var found = false;
  for (final item in items) {
    Object? output;
    if (item.paired != null) {
      output = item.paired!.raw['output'];
    } else if (item.block.kind == 'tool_result') {
      output = item.block.raw['output'];
    }
    final stats = parseDiffStats(output);
    if (stats == null) continue;
    found = true;
    added += stats.added;
    removed += stats.removed;
  }
  if (!found || (added == 0 && removed == 0)) return null;
  return (added: added, removed: removed);
}

bool pairIsPending(ChatDisplayPair item) => _pairIsPending(item);

int workActionCount(List<ChatDisplayPair> items) =>
    items.where(_isWorkSegmentItem).length;
