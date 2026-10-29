import 'dart:async';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';
import 'package:prodavan/core/chat/models/chat_turn.dart';

/// Fake transcript API for the live-tail contract.
///
/// Polls now fetch the **whole current turn** (the same page a reload uses),
/// so each tail call returns the next full snapshot. `after_seq` must never be
/// used: that incremental merge was the source of the reloaded-chat mush.
class _TailApi extends Fake implements ProdavanApi {
  _TailApi({required this.tails, this.older = const {}});

  final List<Map<String, dynamic>> tails;
  final Map<int, Map<String, dynamic>> older;
  int tailIndex = 0;
  int tailCalls = 0;
  final List<int?> calls = [];

  @override
  Future<Map<String, dynamic>> projectChatTranscript({
    required String projectId,
    required String sessionId,
    int limit = 500,
    int? beforeSeq,
    int? afterSeq,
  }) async {
    calls.add(afterSeq ?? beforeSeq);
    if (afterSeq != null) {
      throw StateError('after_seq polling was removed');
    }
    if (beforeSeq != null) return older[beforeSeq] ?? _emptyPage;
    tailCalls++;
    final page = tailIndex < tails.length ? tails[tailIndex] : tails.last;
    tailIndex++;
    return page;
  }

  @override
  Future<List<Map<String, dynamic>>> listPendingApprovals({
    required String projectId,
    required String sessionId,
  }) async => const [];
}

const Map<String, dynamic> _emptyPage = {
  'session_id': 's1',
  'blocks': <dynamic>[],
  'has_more': false,
  'oldest_seq': null,
  'newest_seq': null,
  'total_events': 0,
  'pending_approvals': <dynamic>[],
  'turn_in_progress': false,
};

Map<String, dynamic> _page({
  required List<Map<String, dynamic>> blocks,
  required int newestSeq,
  required bool inProgress,
  bool hasMore = false,
  int? oldestSeq,
}) => {
  'session_id': 's1',
  'blocks': blocks,
  'has_more': hasMore,
  'oldest_seq': oldestSeq ?? 1,
  'newest_seq': newestSeq,
  'total_events': newestSeq,
  'pending_approvals': <dynamic>[],
  'turn_in_progress': inProgress,
};

Map<String, dynamic> _user(String text) => {
  'kind': 'user',
  'text': text,
  'created_at': '2026-10-29T10:00:00Z',
};

Map<String, dynamic> _assistant(String text) => {
  'kind': 'assistant_markdown',
  'text': text,
};

Map<String, dynamic> _toolCall(String id, String name) => {
  'kind': 'tool_call',
  'id': id,
  'name': name,
  'input': <String, dynamic>{},
};

Map<String, dynamic> _toolResult(String id, String name, String output) => {
  'kind': 'tool_result',
  'id': id,
  'name': name,
  'output': output,
};

void main() {
  test('reload mid-turn replaces the open turn from the tail, never merges', () {
    fakeAsync((async) {
      final api = _TailApi(
        tails: [
          _page(
            blocks: [_user('сделай отчёт'), _assistant('partial')],
            newestSeq: 10,
            inProgress: true,
          ),
          _page(
            blocks: [_user('сделай отчёт'), _assistant('partial bar')],
            newestSeq: 11,
            inProgress: true,
          ),
          // Duplicate snapshot with a new seq — must not grow or duplicate.
          _page(
            blocks: [_user('сделай отчёт'), _assistant('partial bar')],
            newestSeq: 12,
            inProgress: true,
          ),
          _page(
            blocks: [_user('сделай отчёт'), _assistant('partial bar')],
            newestSeq: 12,
            inProgress: false,
          ),
        ],
      );
      final controller = ChatSessionController(
        api: api,
        projectId: 'p1',
        sessionId: 's1',
      );

      controller.loadTranscript();
      async.flushMicrotasks();
      expect(controller.turnInProgress, isTrue);
      expect(controller.blocks.length, 2);
      expect(buildChatTurns(controller.visibleBlocks).length, 1);
      final turnId = buildChatTurns(controller.visibleBlocks).single.id;

      async.elapse(const Duration(milliseconds: 2600));
      async.flushMicrotasks();
      expect(controller.blocks.length, 2, reason: 'whole turn replaced');
      expect(controller.blocks.last.text, 'partial bar');
      expect(buildChatTurns(controller.visibleBlocks).single.id, turnId);
      final keys = controller.blocks.map((b) => b.key).toList();
      expect(keys.toSet().length, keys.length);

      async.elapse(const Duration(milliseconds: 2600));
      async.flushMicrotasks();
      expect(controller.blocks.length, 2);
      expect(controller.blocks.last.text, 'partial bar');

      async.elapse(const Duration(milliseconds: 2600));
      async.flushMicrotasks();
      expect(controller.turnInProgress, isFalse);
      expect(api.tailCalls, 4);

      // Polling stopped: no further transcript calls.
      final callsAtDone = api.calls.length;
      async.elapse(const Duration(seconds: 10));
      async.flushMicrotasks();
      expect(api.calls.length, callsAtDone);
      expect(api.calls.every((c) => c == null), isTrue);

      controller.dispose();
    });
  });

  test('turns stay one item when a tool call follows the visible text', () {
    fakeAsync((async) {
      final api = _TailApi(
        tails: [
          _page(
            blocks: [
              _user('проанализируй'),
              _assistant('начало ответа'),
              _toolCall('c1', 'fs.read'),
              _toolResult('c1', 'fs.read', 'ok'),
              _assistant('продолжение ответа'),
            ],
            newestSeq: 20,
            inProgress: true,
          ),
          _page(
            blocks: [
              _user('проанализируй'),
              _assistant('начало ответа'),
              _toolCall('c1', 'fs.read'),
              _toolResult('c1', 'fs.read', 'ok'),
              _toolCall('c2', 'fs.write'),
              _toolResult('c2', 'fs.write', 'ok'),
              _assistant('продолжение ответа'),
            ],
            newestSeq: 22,
            inProgress: true,
          ),
        ],
      );
      final controller = ChatSessionController(
        api: api,
        projectId: 'p1',
        sessionId: 's1',
      );

      controller.loadTranscript();
      async.flushMicrotasks();
      var turns = buildChatTurns(controller.visibleBlocks, turnInProgress: true);
      expect(turns.length, 1);
      expect(turns.single.finalText, 'продолжение ответа');
      expect(turns.single.actionCount, 1);
      final firstId = turns.single.id;

      async.elapse(const Duration(milliseconds: 2600));
      async.flushMicrotasks();
      turns = buildChatTurns(controller.visibleBlocks, turnInProgress: true);
      expect(turns.length, 1);
      expect(turns.single.id, firstId);
      expect(turns.single.finalText, 'продолжение ответа');
      expect(turns.single.actionCount, 2);

      controller.dispose();
    });
  });

  test('tagged final answer survives a reload with its table intact', () {
    const table = '| Позиция | Цена |\n|---|---|\n| A | 10 |\n| B | 20 |';
    final page = _page(
      blocks: [
        _user('таблица'),
        _assistant('Сейчас соберу данные…'),
        _toolCall('c1', 'fs.read'),
        _toolResult('c1', 'fs.read', 'ok'),
        _assistant('<final_answer>$table</final_answer>'),
      ],
      newestSeq: 30,
      inProgress: false,
    );

    final blocks = chatBlocksFromTranscript(page['blocks'] as List);
    final turns = buildChatTurns(blocks);
    expect(turns.length, 1);
    expect(turns.single.finalText, table);
    expect(turns.single.finalText.contains('|---|---|'), isTrue);
    expect(turns.single.work.where((b) => b.kind == 'assistant_markdown').length, 1);

    // The same blocks fetched again (reload) produce the identical turn.
    final reloaded = chatBlocksFromTranscript(page['blocks'] as List);
    final reloadedTurn = buildChatTurns(reloaded).single;
    expect(reloadedTurn.finalText, turns.single.finalText);
    expect(reloadedTurn.id, turns.single.id);
    expect(reloadedTurn.actionCount, turns.single.actionCount);
  });

  test('pagination overlap with id-less blocks drops duplicates by content', () {
    fakeAsync((async) {
      final api = _TailApi(
        tails: [
          _page(
            blocks: [_assistant('b')],
            newestSeq: 10,
            inProgress: false,
          ),
        ],
        older: {
          5: {
            'session_id': 's1',
            'blocks': [_assistant('a'), _assistant('b')],
            'has_more': true,
            'oldest_seq': 1,
            'newest_seq': 4,
            'total_events': 10,
            'pending_approvals': <dynamic>[],
            'turn_in_progress': false,
          },
        },
      );
      final controller = ChatSessionController(
        api: api,
        projectId: 'p1',
        sessionId: 's1',
      );

      controller.loadTranscript();
      async.flushMicrotasks();
      expect(controller.blocks.single.text, 'b');

      controller.hasMoreHistory = true;
      controller.oldestSeq = 5;
      controller.loadOlderTranscript();
      async.flushMicrotasks();

      expect(controller.blocks.map((b) => b.text).toList(), ['a', 'b']);
      final keys = controller.blocks.map((b) => b.key).toList();
      expect(keys.toSet().length, keys.length);
      expect(controller.oldestSeq, 1);
      expect(controller.newestSeq, 10);
      controller.dispose();
    });
  });

  test('block identity is unique across two transcript fetches', () {
    final first = chatBlocksFromTranscript([
      {'kind': 'user', 'text': 'hi'},
    ]);
    final second = chatBlocksFromTranscript([
      {'kind': 'user', 'text': 'hi'},
    ]);
    expect(first.single.key, isNot(second.single.key));
  });
}
