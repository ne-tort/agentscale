import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart'
    show ActivityGroupKind;

typedef ChatDisplayPair = ({ChatBlock block, ChatBlock? paired});

/// One visual row in the chat transcript.
///
/// The timeline is intentionally small: a turn contributes at most ONE
/// collapsed activity aggregation plus its final answer. Everything the agent
/// did on the way to the answer (thinking, tool calls/results, intermediate
/// commentary) lives inside [ChatDisplayActivity].
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

/// Collapsed "agent worked" aggregation for one contiguous run inside a turn.
///
/// Unlike the old per-run group, ALL activity of a turn collapses into this
/// one entry (even a single tool call): the transcript keeps user prompts and
/// final answers open, and everything else behind a single expandable line.
class ChatDisplayActivity extends ChatDisplayEntry {
  const ChatDisplayActivity({
    required this.items,
    required this.streaming,
    required this.actionCount,
    this.diffStats,
  });

  final List<ChatDisplayPair> items;

  /// The turn is still running — header shows «Работаю…» instead of the count.
  final bool streaming;

  /// Thinking + tool actions (blank/soft assistant blocks are not counted).
  final int actionCount;

  /// Aggregated `+/-` badge, computed once per grouping (not per frame).
  final ({int added, int removed})? diffStats;
}

ActivityGroupKind? activityGroupKind(ChatDisplayPair item) {
  final block = item.block;
  if (block.kind == 'thinking') return ActivityGroupKind.thinking;
  if (block.kind != 'tool_call' && block.kind != 'tool_result') return null;

  return switch (toolKindFromPair(
    _toolName(block, item.paired),
    _toolInput(block, item.paired),
  )) {
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
  return block.raw['name'] as String? ??
      paired?.raw['name'] as String? ??
      'tool';
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

bool _isWorkSegmentItem(ChatDisplayPair item) =>
    item.block.kind == 'thinking' || _isActivityPair(item);

bool _pairIsPending(ChatDisplayPair item) =>
    item.block.kind == 'tool_call' && item.paired == null;

/// Pair tool calls with their results in ONE pass.
///
/// The old implementation looked ahead 25 blocks for every call — O(25·n) on
/// every regroup, which is noticeable on tool-heavy turns with hundreds of
/// actions. Wire ids are unique, so an id → first-result index map gives exact
/// pairing in O(n).
List<ChatDisplayPair> mergeToolPairs(List<ChatBlock> blocks) {
  final resultIndexById = <String, int>{};
  for (var i = 0; i < blocks.length; i++) {
    final b = blocks[i];
    if (b.kind == 'tool_result' && b.id.isNotEmpty) {
      resultIndexById.putIfAbsent(b.id, () => i);
    }
  }
  final usedResults = <int>{};
  final out = <ChatDisplayPair>[];
  for (var i = 0; i < blocks.length; i++) {
    if (usedResults.contains(i)) continue;
    final block = blocks[i];
    if (block.kind == 'tool_call') {
      final callId = block.id;
      if (callId.isNotEmpty) {
        final j = resultIndexById[callId];
        if (j != null && j > i && !usedResults.contains(j)) {
          usedResults.add(j);
          out.add((block: block, paired: blocks[j]));
          continue;
        }
      } else if (i + 1 < blocks.length &&
          !usedResults.contains(i + 1) &&
          blocks[i + 1].kind == 'tool_result' &&
          blocks[i + 1].id.isEmpty) {
        usedResults.add(i + 1);
        out.add((block: block, paired: blocks[i + 1]));
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
    return (
      block: ChatBlock(kind: 'thinking', raw: const {'text': ''}),
      paired: null,
    );
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
        '_key': items.first.block.key,
      },
    ),
    paired: null,
  );
}

List<ChatDisplayEntry> _groupThinkingRun(
  List<ChatDisplayPair> items,
  int start,
  int end,
) {
  final slice = items.sublist(start, end);
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

/// Final-answer detection — per turn.
///
/// The final answer is the LAST non-empty assistant message of a turn,
/// provided nothing the agent *did* follows it. A streaming text followed by a
/// tool call is intermediate commentary: it collapses into the activity block,
/// and only the post-tool answer stays open. This is the
/// "все действия, кроме конечного ответа" rule — and it keeps live streaming
/// honest (the growing text is open while it is still the tail of the turn).
Set<int> _finalAnswerIndexes(List<ChatDisplayPair> merged) {
  final answers = <int>{};

  void closeTurn(int start, int endExclusive) {
    var candidate = -1;
    for (var i = endExclusive - 1; i >= start; i--) {
      final b = merged[i].block;
      if (b.kind == 'assistant_markdown' && b.text.trim().isNotEmpty) {
        candidate = i;
        break;
      }
    }
    if (candidate < 0) return;
    for (var i = candidate + 1; i < endExclusive; i++) {
      if (_isWorkSegmentItem(merged[i])) return;
    }
    answers.add(candidate);
  }

  var turnStart = 0;
  for (var i = 0; i < merged.length; i++) {
    if (merged[i].block.kind == 'user') {
      closeTurn(turnStart, i);
      turnStart = i + 1;
    }
  }
  closeTurn(turnStart, merged.length);
  return answers;
}

/// Group a flat block list into the chat timeline.
///
/// * user / approval / plan / subagent / error / system notice — visible rows;
/// * the final assistant answer of EACH turn — visible row;
/// * everything else — one [ChatDisplayActivity] per contiguous run, collapsed.
List<ChatDisplayEntry> groupDisplayEntries(
  List<ChatBlock> blocks, {
  bool turnStreaming = false,
}) {
  final merged = mergeToolPairs(blocks);
  if (merged.isEmpty) return const [];
  final answerIndexes = _finalAnswerIndexes(merged);

  var lastUserIndex = -1;
  for (var i = 0; i < merged.length; i++) {
    if (merged[i].block.kind == 'user') lastUserIndex = i;
  }
  final currentTurnHasAnswer = answerIndexes.any((i) => i > lastUserIndex);

  final out = <ChatDisplayEntry>[];
  var activity = <ChatDisplayPair>[];
  var activityStart = -1;

  void flushActivity() {
    if (activity.isEmpty) return;
    if (!activity.any(_rendersInActivity)) {
      activity = <ChatDisplayPair>[];
      activityStart = -1;
      return;
    }
    final belongsToCurrentTurn = activityStart > lastUserIndex;
    final streaming =
        turnStreaming && belongsToCurrentTurn && !currentTurnHasAnswer;
    out.add(
      ChatDisplayActivity(
        items: List<ChatDisplayPair>.unmodifiable(activity),
        streaming: streaming,
        actionCount: workActionCount(activity),
        diffStats: aggregateDiffStats(activity),
      ),
    );
    activity = <ChatDisplayPair>[];
    activityStart = -1;
  }

  void addToActivity(int index, ChatDisplayPair item) {
    if (activity.isEmpty) activityStart = index;
    activity.add(item);
  }

  for (var i = 0; i < merged.length; i++) {
    final item = merged[i];
    final block = item.block;

    if (answerIndexes.contains(i)) {
      flushActivity();
      out.add(ChatDisplaySingle(item: item));
      continue;
    }

    switch (block.kind) {
      case 'user' ||
          'approval' ||
          'plan' ||
          'subagent' ||
          'error' ||
          'system_notice':
        flushActivity();
        out.add(ChatDisplaySingle(item: item));
        break;
      case 'usage':
      case 'status':
      case 'tool_progress':
      case 'permission_denial':
        // Invisible/auxiliary blocks never create their own row.
        addToActivity(i, item);
        break;
      case 'assistant_markdown':
        // Intermediate commentary or a still-empty streaming shell.
        addToActivity(i, item);
        break;
      default:
        addToActivity(i, item);
        break;
    }
  }
  flushActivity();
  return out;
}

/// Blocks that never render a row of their own.
bool _rendersInActivity(ChatDisplayPair item) => switch (item.block.kind) {
  'usage' || 'status' || 'tool_progress' || 'tool_call_delta' => false,
  _ => true,
};

/// Inner grouping for an expanded activity block (same-kind sub-headers).
List<ChatDisplayEntry> groupInnerWorkItems(List<ChatDisplayPair> items) {
  final out = <ChatDisplayEntry>[];
  var i = 0;
  while (i < items.length) {
    final kind = activityGroupKind(items[i]);
    if (kind == ActivityGroupKind.thinking) {
      var j = i + 1;
      while (j < items.length &&
          activityGroupKind(items[j]) == ActivityGroupKind.thinking) {
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
    final stats = parseDiffStatsCached(output);
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
