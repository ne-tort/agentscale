import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_turn.dart';

ChatBlock _block(String kind, Map<String, dynamic> raw) =>
    ChatBlock(kind: kind, raw: raw);

ChatBlock _user(String text, {String at = '2026-10-29T10:00:00Z'}) =>
    _block('user', {'text': text, 'created_at': at});

ChatBlock _assistant(String text, {String? at}) => _block('assistant_markdown', {
  'text': text,
  if (at != null) 'created_at': at,
});

ChatBlock _thinking(String text) => _block('thinking', {'text': text});

ChatBlock _toolCall(String id, String name) => _block('tool_call', {
  'id': id,
  'name': name,
  'input': <String, dynamic>{},
});

ChatBlock _toolResult(String id, String output) => _block('tool_result', {
  'id': id,
  'name': 'fs.write',
  'output': output,
});

ChatBlock _approval(String id) => _block('approval', {
  'id': id,
  'name': 'shell',
  'input': <String, dynamic>{},
});

void main() {
  test('one turn: one work group plus the tagged final answer', () {
    final blocks = [
      _user('собери отчёт'),
      _thinking('думаю'),
      _toolCall('c1', 'fs.write'),
      _toolResult(
        'c1',
        'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n+more\n',
      ),
      _assistant('Собираю данные…'),
      _assistant(
        '<final_answer># Итог\n\n| Позиция | Цена |\n|---|---|\n| A | 10 |</final_answer>',
        at: '2026-10-29T10:10:00Z',
      ),
    ];

    final turns = buildChatTurns(blocks);
    expect(turns.length, 1);
    final turn = turns.single;
    expect(turn.user?.text, 'собери отчёт');
    expect(turn.work.length, 4);
    expect(turn.finalText, startsWith('# Итог'));
    expect(turn.finalText, contains('|---|---|'));
    expect(turn.actionCount, 2); // thinking + one tool call
    expect(turn.diffStats?.added, 2);
    expect(turn.diffStats?.removed, 1);
    expect(turn.durationMs, 600000);
    expect(turn.running, isFalse);
  });

  test('a trailing tool call cannot hide the final answer', () {
    final blocks = [
      _user('ответ'),
      _assistant('черновик'),
      _assistant('итоговый ответ'),
      _toolCall('c1', 'todo.write'),
      _toolResult('c1', 'ok'),
    ];
    final turn = buildChatTurns(blocks).single;
    expect(turn.finalText, 'итоговый ответ');
    expect(turn.work.any((b) => b.kind == 'assistant_markdown'), isTrue);
    expect(turn.work.any((b) => b.kind == 'tool_result'), isTrue);
    expect(turn.actionCount, 1);
  });

  test('two turns never leak work or answers across the boundary', () {
    final blocks = [
      _user('первый'),
      _thinking('t1'),
      _assistant('<final_answer>ответ один</final_answer>'),
      _user('второй'),
      _assistant('<final_answer>ответ два</final_answer>'),
    ];
    final turns = buildChatTurns(blocks);
    expect(turns.length, 2);
    expect(turns[0].finalText, 'ответ один');
    expect(turns[0].work.single.kind, 'thinking');
    expect(turns[1].finalText, 'ответ два');
    expect(turns[1].work, isEmpty);
  });

  test('the tag may straddle assistant blocks', () {
    final blocks = [
      _user('таблица'),
      _assistant('<final_answer>| a | b |'),
      _toolCall('c1', 'fs.read'),
      _assistant('|---|---|</final_answer>'),
    ];
    final turn = buildChatTurns(blocks).single;
    expect(turn.finalText, '| a | b ||---|---|');
  });

  test('only an unresolved approval keeps the turn expanded', () {
    final blocks = [
      _user('сделай'),
      _assistant('работаю'),
      _approval('a1'),
    ];
    final pending = buildChatTurns(
      blocks,
      pendingApprovalIds: {'a1'},
    ).single;
    expect(pending.hasPendingApproval, isTrue);

    final resolved = buildChatTurns(blocks).single;
    expect(resolved.hasPendingApproval, isFalse);
  });

  test('a running turn does not flash a preamble as the final answer', () {
    final blocks = [
      _user('сделай'),
      _assistant('сейчас посмотрю файлы'),
      _toolCall('c1', 'fs.read'),
      _toolResult('c1', 'ok'),
    ];
    final turn = buildChatTurns(blocks, turnInProgress: true).single;
    expect(turn.running, isTrue);
    expect(turn.finalText, isEmpty);
    expect(turn.work.any((b) => b.text == 'сейчас посмотрю файлы'), isTrue);
  });

  test('an unclosed tag streams a partial answer and marks the turn running', () {
    final blocks = [
      _user('стрим', at: '2026-10-29T10:00:00Z'),
      _assistant('<final_answer>первые строки', at: '2026-10-29T10:01:00Z'),
    ];
    final turn = buildChatTurns(
      blocks,
      turnInProgress: true,
      now: DateTime.utc(2026, 10, 29, 10, 2),
    ).single;
    expect(turn.running, isTrue);
    expect(turn.finalText, 'первые строки');
    expect(turn.durationMs, 120000);
  });

  test('an empty streaming shell is not an answer row', () {
    final blocks = [
      _user('пусто'),
      _assistant('', at: '2026-10-29T10:00:01Z'),
    ];
    final turn = buildChatTurns(blocks, turnInProgress: true).single;
    expect(turn.finalText, isEmpty);
    expect(turn.work, isEmpty);
    expect(turn.hasFinal, isFalse);
  });
}
