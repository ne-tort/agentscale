import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';

void main() {
  test('applyStreamEvent accumulates incremental text_delta into streaming assistant block', () {
    var blocks = <ChatBlock>[];
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'Hel'}});
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'lo'}});
    expect(blocks.length, 1);
    expect(blocks.first.kind, 'assistant_markdown');
    expect(blocks.first.text, 'Hello');
    expect(blocks.first.isStreaming, isTrue);
  });

  test('applyStreamEvent dedupes cumulative text_delta', () {
    var blocks = <ChatBlock>[];
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'При'}});
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'Привет'}});
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'Привет!'}});
    expect(blocks.single.text, 'Привет!');
  });

  test('normalizeTextDelta appends incremental chunks', () {
    var cum = '';
    var r = normalizeTextDelta(cum, 'Hel');
    cum = r.cumulative;
    r = normalizeTextDelta(cum, 'lo');
    expect(r.cumulative, 'Hello');
  });

  test('normalizeTextDelta merges suffix/prefix overlap', () {
    var r = normalizeTextDelta('Проверка', 'роверка прошла');
    expect(r.cumulative, 'Проверка прошла');
    r = normalizeTextDelta(r.cumulative, ' успешно');
    expect(r.cumulative, 'Проверка прошла успешно');
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

  test('applyStreamEvent merges usage blocks', () {
    var blocks = applyStreamEvent([], {
      'type': 'usage',
      'data': {'input_tokens': 10},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'usage',
      'data': {'output_tokens': 5},
    });
    expect(blocks.length, 1);
    expect(blocks.single.kind, 'usage');
    expect(blocks.single.raw['input_tokens'], 10);
    expect(blocks.single.raw['output_tokens'], 5);
  });
}
