import 'dart:async';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';

class _PollApi extends Fake implements ProdavanApi {
  _PollApi(this.pages);

  /// Keyed by `after_seq`; `null` = the full transcript call.
  final Map<int?, Map<String, dynamic>> pages;
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
      return pages[afterSeq] ?? pages[-1] ?? _emptyPage;
    }
    if (beforeSeq != null) {
      return pages[beforeSeq] ?? _emptyPage;
    }
    return pages[null] ?? _emptyPage;
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
  int? oldestSeq,
}) => {
  'session_id': 's1',
  'blocks': blocks,
  'has_more': false,
  'oldest_seq': oldestSeq ?? 1,
  'newest_seq': newestSeq,
  'total_events': newestSeq,
  'pending_approvals': <dynamic>[],
  'turn_in_progress': inProgress,
};

void main() {
  test('reload mid-turn tails with after_seq, never duplicates keys/text, stops at done', () {
    fakeAsync((async) {
      final api = _PollApi({
        // Initial transcript: user prompt + partial answer.
        null: _page(
          blocks: [
            {'kind': 'user', 'text': 'сделай отчёт'},
            {'kind': 'assistant_markdown', 'text': 'partial'},
          ],
          newestSeq: 10,
          inProgress: true,
        ),
        // First poll: the answer grew.
        10: _page(
          blocks: [
            {'kind': 'assistant_markdown', 'text': ' bar'},
          ],
          newestSeq: 11,
          inProgress: true,
        ),
        // Second poll: a duplicated frame (same text, new seq) must be a no-op.
        11: _page(
          blocks: [
            {'kind': 'assistant_markdown', 'text': ' bar'},
          ],
          newestSeq: 12,
          inProgress: true,
        ),
        // Third poll: the turn finished.
        12: _page(blocks: const [], newestSeq: 12, inProgress: false),
      });
      final controller = ChatSessionController(
        api: api,
        projectId: 'p1',
        sessionId: 's1',
      );

      controller.loadTranscript();
      async.flushMicrotasks();
      expect(controller.turnInProgress, isTrue);
      expect(controller.blocks.length, 2);

      async.elapse(const Duration(milliseconds: 2600));
      async.flushMicrotasks();
      expect(
        controller.blocks.length,
        2,
        reason: 'answer merged, not duplicated',
      );
      expect(controller.blocks.last.text, 'partial bar');
      final keys = controller.blocks.map((b) => b.key).toList();
      expect(
        keys.toSet().length,
        keys.length,
        reason: 'polled keys must be unique',
      );

      async.elapse(const Duration(milliseconds: 2600));
      async.flushMicrotasks();
      // Duplicate replay: identical chunk after "partial bar" is ignored.
      expect(controller.blocks.length, 2);
      expect(controller.blocks.last.text, 'partial bar');

      async.elapse(const Duration(milliseconds: 2600));
      async.flushMicrotasks();
      expect(controller.turnInProgress, isFalse);
      expect(api.calls, containsAllInOrder([10, 11, 12]));

      // Polling stopped: no further transcript calls.
      final callsAtDone = api.calls.length;
      async.elapse(const Duration(seconds: 10));
      async.flushMicrotasks();
      expect(api.calls.length, callsAtDone);

      controller.dispose();
    });
  });

  test(
    'pagination overlap with id-less blocks drops duplicates by content',
    () {
      fakeAsync((async) {
        final api = _PollApi({
          null: _page(
            blocks: [
              {'kind': 'assistant_markdown', 'text': 'b'},
            ],
            newestSeq: 10,
            inProgress: false,
          ),
          5: {
            'session_id': 's1',
            'blocks': [
              {'kind': 'assistant_markdown', 'text': 'a'},
              {'kind': 'assistant_markdown', 'text': 'b'},
            ],
            'has_more': true,
            'oldest_seq': 1,
            // An older page's "newest" is page-local — adopting it would rewind
            // the live poller. The controller must keep the tail cursor.
            'newest_seq': 4,
            'total_events': 10,
            'pending_approvals': <dynamic>[],
            'turn_in_progress': false,
          },
        });
        final controller = ChatSessionController(
          api: api,
          projectId: 'p1',
          sessionId: 's1',
        );

        controller.loadTranscript();
        async.flushMicrotasks();
        // Simulate has_more + cursor so loadOlder is allowed.
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
    },
  );

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
