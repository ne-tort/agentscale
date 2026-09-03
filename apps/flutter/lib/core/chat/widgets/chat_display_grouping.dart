import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';

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

bool _isHardBoundary(ChatBlock block) {
  return switch (block.kind) {
    'user' || 'approval' || 'plan' || 'usage' || 'error' || 'subagent' => true,
    'assistant_markdown' => block.text.trim().isNotEmpty && !block.isStreaming,
    _ => false,
  };
}

bool _pairIsPending(ChatDisplayPair item) {
  if (item.block.kind == 'tool_call' && item.paired == null) return true;
  if (item.paired == null && item.block.kind == 'tool_call') return true;
  return false;
}

bool _workSessionStreaming(List<ChatDisplayPair> items, bool turnStreaming) {
  if (!turnStreaming) return false;
  return items.any(_pairIsPending);
}

List<ChatDisplayPair> mergeToolPairs(List<ChatBlock> blocks) {
  final out = <ChatDisplayPair>[];
  var i = 0;
  while (i < blocks.length) {
    final paired = pairedToolResultFor(blocks, i);
    if (paired != null) {
      out.add((block: blocks[i], paired: paired));
      i += 2;
      continue;
    }
    if (isMergedToolResult(blocks, i)) {
      i++;
      continue;
    }
    out.add((block: blocks[i], paired: null));
    i++;
  }
  return out;
}

List<ChatDisplayEntry> _groupThinkingRun(List<ChatDisplayPair> merged, int start, int end) {
  if (end - start >= 2) {
    return [
      ChatDisplayGroup(
        kind: ActivityGroupKind.thinking,
        items: merged.sublist(start, end),
      ),
    ];
  }
  return [ChatDisplaySingle(item: merged[start])];
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

List<ChatDisplayEntry> groupDisplayEntries(List<ChatBlock> blocks, {bool turnStreaming = false}) {
  final merged = mergeToolPairs(blocks);
  final out = <ChatDisplayEntry>[];
  var i = 0;
  while (i < merged.length) {
    final block = merged[i].block;

    if (_isThinkingBlock(block)) {
      var j = i + 1;
      while (j < merged.length && _isThinkingBlock(merged[j].block)) {
        j++;
      }
      out.addAll(_groupThinkingRun(merged, i, j));
      i = j;
      continue;
    }

    if (_isHardBoundary(block) || (!_isActivityPair(merged[i]) && block.kind != 'tool_call')) {
      out.add(ChatDisplaySingle(item: merged[i]));
      i++;
      continue;
    }

    if (_isActivityPair(merged[i])) {
      var j = i;
      while (j < merged.length && _isActivityPair(merged[j])) {
        j++;
      }
      final run = merged.sublist(i, j);
      if (run.length >= 2) {
        out.add(ChatDisplayWorkSession(
          items: run,
          streaming: _workSessionStreaming(run, turnStreaming),
        ));
      } else {
        out.add(ChatDisplaySingle(item: run.first));
      }
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
    if (kind == null || kind == ActivityGroupKind.thinking) {
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
