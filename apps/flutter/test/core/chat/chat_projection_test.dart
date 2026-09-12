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

  test('applyStreamEvent does not swallow suffix-overlap incremental chunks', () {
    // Wire is already incremental; "lo" after "Hello" must append, not overlap-merge away.
    var blocks = applyStreamEvent([], {
      'type': 'text_delta',
      'data': {'text': 'Hello'},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'text_delta',
      'data': {'text': 'lo'},
    });
    expect(blocks.single.text, 'Hellolo');
  });

  test('applyStreamEvent does not swallow cyrillic suffix overlap', () {
    var blocks = applyStreamEvent([], {
      'type': 'text_delta',
      'data': {'text': 'Найденные'},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'text_delta',
      'data': {'text': 'денные'},
    });
    expect(blocks.single.text, 'Найденныеденные');
  });

  test('normalizeTextDelta still merges cumulative SDK chunks (API/transcript)', () {
    var cum = '';
    var r = normalizeTextDelta(cum, 'Hel');
    cum = r.cumulative;
    r = normalizeTextDelta(cum, 'lo');
    expect(r.cumulative, 'Hello');
    r = normalizeTextDelta('Проверка', 'роверка прошла');
    expect(r.cumulative, 'Проверка прошла');
  });

  test('finalizeTurnBlocks clears streaming flag', () {
    final blocks = [
      ChatBlock(kind: 'assistant_markdown', raw: {'text': 'Hi', '_streaming': true}),
    ];
    final done = finalizeTurnBlocks(blocks);
    expect(done.first.isStreaming, isFalse);
  });

  test('tool_call closes streaming assistant so later text is a new block', () {
    var blocks = applyStreamEvent([], {
      'type': 'text_delta',
      'data': {'text': 'Before '},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'tool_call',
      'data': {'id': 't1', 'name': 'Read', 'input': {'path': 'a.md'}},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'text_delta',
      'data': {'text': 'After'},
    });
    expect(blocks.length, 3);
    expect(blocks[0].kind, 'assistant_markdown');
    expect(blocks[0].text, 'Before ');
    expect(blocks[0].isStreaming, isFalse);
    expect(blocks[1].kind, 'tool_call');
    expect(blocks[2].kind, 'assistant_markdown');
    expect(blocks[2].text, 'After');
    expect(blocks[2].isStreaming, isTrue);
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
