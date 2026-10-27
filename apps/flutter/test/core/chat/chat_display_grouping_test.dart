import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/chat/widgets/chat_display_grouping.dart';

ChatBlock _toolCall(String name, {Map<String, dynamic>? input, String? id}) {
  return ChatBlock(
    kind: 'tool_call',
    raw: {'id': id ?? name, 'name': name, 'input': input ?? const {}},
  );
}

ChatBlock _toolResult(String name, {Object? output, String? id}) {
  return ChatBlock(
    kind: 'tool_result',
    raw: {'id': id ?? name, 'name': name, 'output': output},
  );
}

ChatBlock _assistant(String text, {bool streaming = false}) => ChatBlock(
  kind: 'assistant_markdown',
  raw: {'text': text, if (streaming) '_streaming': true},
);

void main() {
  test('mergeToolPairs merges call+result with same id', () {
    final blocks = [
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit', output: '+1\n-0'),
    ];
    final merged = mergeToolPairs(blocks);
    expect(merged.length, 1);
    expect(merged.first.paired?.kind, 'tool_result');
  });

  test('all actions of a turn collapse into ONE activity entry', () {
    final blocks = [
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit'),
      _toolCall('delete', input: {'path': 'b.py'}),
      _toolResult('delete'),
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 2);
    expect(entries.first, isA<ChatDisplayActivity>());
    final activity = entries.first as ChatDisplayActivity;
    expect(activity.items.length, 2);
    expect(activity.actionCount, 2);
    expect(activity.streaming, isFalse);
    expect(entries.last, isA<ChatDisplaySingle>());
  });

  test('a single tool call is collapsed too — actions never stay open', () {
    final blocks = [
      _toolCall('glob', input: {'globPattern': '*.dart'}),
      _toolResult('glob'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 1);
    expect(entries.first, isA<ChatDisplayActivity>());
    expect((entries.first as ChatDisplayActivity).items.length, 1);
  });

  test('final answer stays visible; prior actions collapse', () {
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'question'}),
      _toolCall('glob', input: {'globPattern': '*.dart'}),
      _toolResult('glob'),
      _toolCall('grep', input: {'pattern': 'foo'}),
      _toolResult('grep'),
      _assistant('Done.'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 3);
    expect(entries[0], isA<ChatDisplaySingle>()); // user
    expect(entries[1], isA<ChatDisplayActivity>()); // actions
    expect(entries[2], isA<ChatDisplaySingle>()); // final answer
    expect(entries[2].whenSingle().block.text, 'Done.');
    expect((entries[1] as ChatDisplayActivity).actionCount, 2);
  });

  test('intermediate assistant commentary collapses when work follows it', () {
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'question'}),
      _assistant('Let me check the files.'),
      _toolCall('glob', input: {'globPattern': '*.dart'}),
      _toolResult('glob'),
      _assistant('Final answer.'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 3);
    final activity = entries[1] as ChatDisplayActivity;
    expect(activity.items.first.block.text, 'Let me check the files.');
    expect(activity.actionCount, 1);
    expect(entries[2].whenSingle().block.text, 'Final answer.');
  });

  test('assistant text followed by work is NOT the final answer', () {
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'question'}),
      _assistant('I will inspect it.'),
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit'),
    ];
    final entries = groupDisplayEntries(blocks, turnStreaming: true);
    expect(entries.length, 2); // user + everything else collapsed
    expect(entries[1], isA<ChatDisplayActivity>());
    final activity = entries[1] as ChatDisplayActivity;
    expect(activity.streaming, isTrue);
    expect(
      activity.items.any((i) => i.block.kind == 'assistant_markdown'),
      isTrue,
    );
  });

  test('thinking is part of the activity and is counted', () {
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      ChatBlock(kind: 'thinking', raw: {'text': 'a'}),
      ChatBlock(kind: 'thinking', raw: {'text': 'b'}),
      _toolCall('edit', input: {'path': 'x.py'}),
      _toolResult('edit'),
      _assistant('answer'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 3);
    final activity = entries[1] as ChatDisplayActivity;
    expect(activity.items.length, 3);
    expect(activity.actionCount, 3);
  });

  test('streaming final answer stays open while it is the turn tail', () {
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit'),
      _assistant('partial answer', streaming: true),
    ];
    final entries = groupDisplayEntries(blocks, turnStreaming: true);
    expect(entries.length, 3);
    expect(entries.last, isA<ChatDisplaySingle>());
    expect(entries.last.whenSingle().block.text, 'partial answer');
  });

  test('empty streaming assistant does not create a final answer row', () {
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      _assistant('', streaming: true),
      _toolCall('glob', input: {'globPattern': '*.dart'}),
      _toolResult('glob'),
    ];
    final entries = groupDisplayEntries(blocks, turnStreaming: true);
    expect(entries.length, 2);
    expect(entries.last, isA<ChatDisplayActivity>());
  });

  test('approvals and errors stay visible between activity runs', () {
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      _toolCall('shell'),
      ChatBlock(kind: 'approval', raw: {'id': 'a1', 'name': 'shell'}),
      _toolCall('edit'),
      _toolResult('edit'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 4);
    expect(entries[1], isA<ChatDisplayActivity>());
    expect(entries[2], isA<ChatDisplaySingle>());
    expect(entries[2].whenSingle().block.kind, 'approval');
    expect(entries[3], isA<ChatDisplayActivity>());
  });

  test('groupInnerWorkItems groups same-kind deletes', () {
    final items = [
      (
        block: _toolCall('delete', input: {'path': 'a.py'}),
        paired: _toolResult('delete'),
      ),
      (
        block: _toolCall('delete', input: {'path': 'b.py'}),
        paired: _toolResult('delete'),
      ),
    ];
    final inner = groupInnerWorkItems(items);
    expect(inner.length, 1);
    expect(inner.first, isA<ChatDisplayGroup>());
    expect(
      (inner.first as ChatDisplayGroup).kind,
      ActivityGroupKind.fileDelete,
    );
  });

  test('groupInnerWorkItems merges consecutive thinking into one Single', () {
    final items = [
      (
        block: ChatBlock(
          kind: 'thinking',
          raw: {'text': 'a', 'duration_ms': 100},
        ),
        paired: null,
      ),
      (
        block: ChatBlock(
          kind: 'thinking',
          raw: {'text': 'b', 'duration_ms': 2500},
        ),
        paired: null,
      ),
      (block: _toolCall('edit'), paired: _toolResult('edit')),
    ];
    final inner = groupInnerWorkItems(items);
    expect(inner.length, 2);
    expect(inner.first, isA<ChatDisplaySingle>());
    final merged = (inner.first as ChatDisplaySingle).item.block;
    expect(merged.kind, 'thinking');
    expect(merged.text, 'a\nb');
    expect(merged.raw['duration_ms'], 2500);
  });

  test('mergeToolPairs matches result by id across a gap', () {
    final blocks = [
      _toolCall('edit', id: 't1', input: {'path': 'a.py'}),
      ChatBlock(kind: 'thinking', raw: {'text': 'mid'}),
      _toolResult('edit', id: 't1', output: '+1'),
    ];
    final merged = mergeToolPairs(blocks);
    expect(merged.length, 2);
    expect(merged.first.paired?.raw['id'], 't1');
    expect(
      merged.every((p) => p.block.kind != 'tool_result' || p.paired != null),
      isTrue,
    );
  });

  test('mergeThinkingPairs concatenates text and takes max duration', () {
    final merged = mergeThinkingPairs([
      (
        block: ChatBlock(
          kind: 'thinking',
          raw: {'text': 'one', 'duration_ms': 10},
        ),
        paired: null,
      ),
      (
        block: ChatBlock(
          kind: 'thinking',
          raw: {'text': 'two', 'duration_ms': 99},
        ),
        paired: null,
      ),
    ]);
    expect(merged.block.text, 'one\ntwo');
    expect(merged.block.raw['duration_ms'], 99);
  });

  test('mergeThinkingPairs keeps the first block key for widget identity', () {
    final first = createLiveBlock('thinking', {'text': 'one'});
    final second = createLiveBlock('thinking', {'text': 'two'});
    final merged = mergeThinkingPairs([
      (block: first, paired: null),
      (block: second, paired: null),
    ]);
    expect(merged.block.key, first.key);
    expect(merged.block.key, isNot(second.key));
  });

  test('aggregateDiffStats sums +/- across group items', () {
    final items = [
      (
        block: _toolCall('edit'),
        paired: _toolResult(
          'edit',
          output: {'lines_added': 2, 'lines_removed': 1},
        ),
      ),
      (
        block: _toolCall('write'),
        paired: _toolResult(
          'write',
          output: {'lines_added': 3, 'lines_removed': 0},
        ),
      ),
    ];
    final stats = aggregateDiffStats(items);
    expect(stats?.added, 5);
    expect(stats?.removed, 1);
  });

  test('activity content hash notices a middle tool-call replacement', () {
    final first = _toolCall('read', id: 'a', input: {'path': 'one'});
    final second = _toolCall('glob', id: 'b', input: {'globPattern': '*.dart'});
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      first,
      second,
    ];
    final before = groupDisplayEntries(blocks)[1] as ChatDisplayActivity;
    // Same keys/count/last item, only an earlier item's payload changed (a
    // tool-call delta placeholder being replaced by the full call).
    final replaced = first.copyWithRaw({'input': {'path': 'two'}});
    final after = groupDisplayEntries([
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      replaced,
      second,
    ])[1] as ChatDisplayActivity;
    expect(before.items.length, after.items.length);
    expect(before.items.first.block.key, after.items.first.block.key);
    expect(before.contentHash, isNot(after.contentHash));
  });

  test('an empty streaming shell does not create an activity row', () {
    final blocks = [
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      _assistant('', streaming: true),
    ];
    final entries = groupDisplayEntries(blocks, turnStreaming: true);
    expect(entries.length, 1);
    expect(entries.single, isA<ChatDisplaySingle>());
  });

  test('usage blocks are absent after attach — no standalone usage entry', () {
    final blocks = attachUsageToAssistant([
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      ChatBlock(kind: 'assistant_markdown', raw: {'text': 'answer'}),
      ChatBlock(
        kind: 'usage',
        raw: {'input_tokens': 1, 'output_tokens': 2, 'model': 'm'},
      ),
    ]);
    expect(blocks.where((b) => b.kind == 'usage'), isEmpty);
    expect((blocks[1].raw['usage'] as Map)['input_tokens'], 1);
    final entries = groupDisplayEntries(blocks);
    // user + final answer singles only; no usage-driven entry appears.
    expect(entries.length, 2);
    expect(
      entries.whereType<ChatDisplaySingle>().map((e) => e.item.block.kind),
      everyElement(isNot('usage')),
    );
  });
}

extension on ChatDisplayEntry {
  ChatDisplayPair whenSingle() => (this as ChatDisplaySingle).item;
}
