import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';

void main() {
  test('applyStreamEvent accumulates text_delta into streaming assistant block', () {
    var blocks = <ChatBlock>[];
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'Hel'}});
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'lo'}});
    expect(blocks.length, 1);
    expect(blocks.first.kind, 'assistant_markdown');
    expect(blocks.first.text, 'Hello');
    expect(blocks.first.isStreaming, isTrue);
  });

  test('finalizeTurnBlocks clears streaming flag', () {
    final blocks = [
      ChatBlock(kind: 'assistant_markdown', raw: {'text': 'Hi', '_streaming': true}),
    ];
    final done = finalizeTurnBlocks(blocks);
    expect(done.first.isStreaming, isFalse);
  });

  test('applyStreamEvent adds tool_call block', () {
    final blocks = applyStreamEvent([], {
      'type': 'tool_call',
      'data': {'id': 't1', 'name': 'Read', 'input': {'path': 'a.md'}},
    });
    expect(blocks.single.kind, 'tool_call');
    expect(blocks.single.raw['name'], 'Read');
  });
}
