import 'package:prodavan/core/chat/models/chat_block.dart';
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

ActivityGroupKind? activityGroupKind(ChatDisplayPair item) {
  final block = item.block;
  if (block.kind == 'thinking') return ActivityGroupKind.thinking;
  if (block.kind != 'tool_call' && block.kind != 'tool_result') return null;

  final name = _toolName(block, item.paired);
  final input = _toolInput(block, item.paired);
  final lower = name.toLowerCase();
  if (lower.startsWith('mcp') || lower.contains('mcp__')) return ActivityGroupKind.mcp;
  switch (lower) {
    case 'edit':
    case 'write':
    case 'strreplace':
      return ActivityGroupKind.fileEdit;
    case 'read':
    case 'read_file':
      return ActivityGroupKind.fileRead;
    case 'shell':
    case 'bash':
    case 'run_terminal_cmd':
      return ActivityGroupKind.command;
    default:
      // Only group known activity buckets.
      final p = toolActivityPresentation(name, input);
      if (p.label.startsWith('Изменён') || p.label.startsWith('Changed')) {
        return ActivityGroupKind.fileEdit;
      }
      if (p.label.startsWith('Прочитан') || p.label.startsWith('Read')) {
        return ActivityGroupKind.fileRead;
      }
      if (p.label.startsWith('Запущена') || p.label.startsWith('Ran')) {
        return ActivityGroupKind.command;
      }
      if (p.label.startsWith('mcp')) return ActivityGroupKind.mcp;
      return null;
  }
}

String _toolName(ChatBlock block, ChatBlock? paired) {
  return block.raw['name'] as String? ?? paired?.raw['name'] as String? ?? 'tool';
}

Map<String, dynamic> _toolInput(ChatBlock block, ChatBlock? paired) {
  final raw = block.raw['input'] ?? paired?.raw['input'];
  if (raw is Map) return Map<String, dynamic>.from(raw);
  return const {};
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

List<ChatDisplayEntry> groupDisplayEntries(List<ChatBlock> blocks) {
  final merged = mergeToolPairs(blocks);
  final out = <ChatDisplayEntry>[];
  var i = 0;
  while (i < merged.length) {
    final kind = activityGroupKind(merged[i]);
    if (kind == null) {
      out.add(ChatDisplaySingle(item: merged[i]));
      i++;
      continue;
    }
    var j = i + 1;
    while (j < merged.length && activityGroupKind(merged[j]) == kind) {
      j++;
    }
    if (j - i >= 2) {
      out.add(ChatDisplayGroup(kind: kind, items: merged.sublist(i, j)));
    } else {
      out.add(ChatDisplaySingle(item: merged[i]));
    }
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
