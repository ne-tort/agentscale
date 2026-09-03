import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
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

  test('groupInnerWorkItems groups thinking inside work session', () {
    final items = [
      (block: ChatBlock(kind: 'thinking', raw: {'text': 'a'}), paired: null),
      (block: ChatBlock(kind: 'thinking', raw: {'text': 'b'}), paired: null),
      (block: _toolCall('edit'), paired: _toolResult('edit')),
    ];
    final inner = groupInnerWorkItems(items);
    expect(inner.length, 2);
    expect(inner.first, isA<ChatDisplayGroup>());
    expect((inner.first as ChatDisplayGroup).kind, ActivityGroupKind.thinking);
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
}
