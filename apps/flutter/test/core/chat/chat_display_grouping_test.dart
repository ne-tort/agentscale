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

  test('groupDisplayEntries wraps mixed tools in WorkSession', () {
    final blocks = [
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit'),
      _toolCall('delete', input: {'path': 'b.py'}),
      _toolResult('delete'),
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 2);
    expect(entries.first, isA<ChatDisplayWorkSession>());
    final session = entries.first as ChatDisplayWorkSession;
    expect(session.items.length, 2);
    expect(entries.last, isA<ChatDisplaySingle>());
  });

  test('groupDisplayEntries single tool stays single', () {
    final blocks = [
      _toolCall('glob', input: {'globPattern': '*.dart'}),
      _toolResult('glob'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 1);
    expect(entries.first, isA<ChatDisplaySingle>());
  });

  test('groupDisplayEntries assistant text breaks work session', () {
    final blocks = [
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit'),
      ChatBlock(kind: 'assistant_markdown', raw: {'text': 'Done.'}),
      _toolCall('glob', input: {'globPattern': '*.dart'}),
      _toolResult('glob'),
      _toolCall('grep', input: {'pattern': 'foo'}),
      _toolResult('grep'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 3);
    expect(entries[0], isA<ChatDisplaySingle>());
    expect(entries[1], isA<ChatDisplaySingle>());
    expect(entries[2], isA<ChatDisplayWorkSession>());
  });

  test('groupDisplayEntries includes thinking in WorkSession', () {
    final blocks = [
      ChatBlock(kind: 'thinking', raw: {'text': 'a'}),
      ChatBlock(kind: 'thinking', raw: {'text': 'b'}),
      _toolCall('edit', input: {'path': 'x.py'}),
      _toolResult('edit'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 1);
    expect(entries.first, isA<ChatDisplayWorkSession>());
    expect((entries.first as ChatDisplayWorkSession).items.length, 3);
  });

  test('groupDisplayEntries thinking plus one tool stays singles', () {
    final blocks = [
      ChatBlock(kind: 'thinking', raw: {'text': 'solo'}),
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 2);
    expect(entries.every((e) => e is ChatDisplaySingle), isTrue);
  });

  test('groupInnerWorkItems groups same-kind deletes', () {
    final items = [
      (block: _toolCall('delete', input: {'path': 'a.py'}), paired: _toolResult('delete')),
      (block: _toolCall('delete', input: {'path': 'b.py'}), paired: _toolResult('delete')),
    ];
    final inner = groupInnerWorkItems(items);
    expect(inner.length, 1);
    expect(inner.first, isA<ChatDisplayGroup>());
    expect((inner.first as ChatDisplayGroup).kind, ActivityGroupKind.fileDelete);
  });

  test('groupInnerWorkItems merges consecutive thinking into one Single', () {
    final items = [
      (block: ChatBlock(kind: 'thinking', raw: {'text': 'a', 'duration_ms': 100}), paired: null),
      (block: ChatBlock(kind: 'thinking', raw: {'text': 'b', 'duration_ms': 2500}), paired: null),
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
    expect(merged.every((p) => p.block.kind != 'tool_result' || p.paired != null), isTrue);
  });

  test('streaming empty assistant does not cut WorkSession', () {
    final blocks = [
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit'),
      ChatBlock(kind: 'assistant_markdown', raw: {'text': '', '_streaming': true}),
      _toolCall('glob', input: {'globPattern': '*.dart'}),
      _toolResult('glob'),
    ];
    final entries = groupDisplayEntries(blocks, turnStreaming: true);
    expect(entries.length, 1);
    expect(entries.first, isA<ChatDisplayWorkSession>());
    expect(workActionCount((entries.first as ChatDisplayWorkSession).items), 2);
  });

  test('empty assistant markdown does not cut WorkSession', () {
    final blocks = [
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit'),
      ChatBlock(kind: 'assistant_markdown', raw: {'text': '   '}),
      _toolCall('glob', input: {'globPattern': '*.dart'}),
      _toolResult('glob'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 1);
    expect(entries.first, isA<ChatDisplayWorkSession>());
  });

  test('mergeThinkingPairs concatenates text and takes max duration', () {
    final merged = mergeThinkingPairs([
      (block: ChatBlock(kind: 'thinking', raw: {'text': 'one', 'duration_ms': 10}), paired: null),
      (block: ChatBlock(kind: 'thinking', raw: {'text': 'two', 'duration_ms': 99}), paired: null),
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
        paired: _toolResult('edit', output: {'lines_added': 2, 'lines_removed': 1}),
      ),
      (
        block: _toolCall('write'),
        paired: _toolResult('write', output: {'lines_added': 3, 'lines_removed': 0}),
      ),
    ];
    final stats = aggregateDiffStats(items);
    expect(stats?.added, 5);
    expect(stats?.removed, 1);
  });

  test('usage blocks are absent after attach — no standalone usage entry', () {
    final blocks = attachUsageToAssistant([
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
      ChatBlock(kind: 'assistant_markdown', raw: {'text': 'answer'}),
      ChatBlock(kind: 'usage', raw: {'input_tokens': 1, 'output_tokens': 2, 'model': 'm'}),
    ]);
    expect(blocks.where((b) => b.kind == 'usage'), isEmpty);
    expect((blocks[1].raw['usage'] as Map)['input_tokens'], 1);
    final entries = groupDisplayEntries(blocks);
    // user + assistant singles only; no usage-driven entry appears.
    expect(entries.length, 2);
    expect(
      entries.whereType<ChatDisplaySingle>().map((e) => e.item.block.kind),
      everyElement(isNot('usage')),
    );
  });
}
