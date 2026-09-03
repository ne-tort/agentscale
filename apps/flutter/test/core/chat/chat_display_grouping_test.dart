import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/chat/widgets/chat_display_grouping.dart';

ChatBlock _toolCall(String name, {Map<String, dynamic>? input}) {
  return ChatBlock(
    kind: 'tool_call',
    raw: {'id': name, 'name': name, 'input': input ?? const {}},
  );
}

ChatBlock _toolResult(String name, {Object? output}) {
  return ChatBlock(
    kind: 'tool_result',
    raw: {'id': name, 'name': name, 'output': output},
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

  test('groupDisplayEntries groups consecutive file edits', () {
    final blocks = [
      _toolCall('edit', input: {'path': 'a.py'}),
      _toolResult('edit'),
      _toolCall('write', input: {'path': 'b.py'}),
      _toolResult('write'),
      ChatBlock(kind: 'user', raw: {'text': 'hi'}),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 2);
    expect(entries.first, isA<ChatDisplayGroup>());
    final group = entries.first as ChatDisplayGroup;
    expect(group.kind, ActivityGroupKind.fileEdit);
    expect(group.items.length, 2);
    expect(entries.last, isA<ChatDisplaySingle>());
  });

  test('groupDisplayEntries does not mix thinking with tools', () {
    final blocks = [
      ChatBlock(kind: 'thinking', raw: {'text': 'a'}),
      ChatBlock(kind: 'thinking', raw: {'text': 'b'}),
      _toolCall('edit', input: {'path': 'x.py'}),
      _toolResult('edit'),
    ];
    final entries = groupDisplayEntries(blocks);
    expect(entries.length, 2);
    expect((entries[0] as ChatDisplayGroup).kind, ActivityGroupKind.thinking);
    expect(entries[1], isA<ChatDisplaySingle>());
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
