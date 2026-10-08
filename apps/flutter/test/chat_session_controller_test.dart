import 'dart:async';

import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';

class _FakeApi extends Fake implements ProdavanApi {
  _FakeApi({this.stream, this.transcript});

  final StreamController<Map<String, dynamic>>? stream;
  Object? cancelError;
  int cancelCalls = 0;

  /// Responses for projectChatTranscript, keyed by afterSeq (null = full load).
  Map<int?, Map<String, dynamic>>? transcript;
  final List<int?> transcriptCalls = [];

  @override
  ProjectChatStreamHandle projectChatStream({
    required String projectId,
    required String text,
    String? sessionId,
    String? model,
    List<String> attachmentRefs = const [],
  }) {
    final stream = this.stream;
    if (stream == null) {
      throw StateError('no fake stream configured');
    }
    return ProjectChatStreamHandle(
      stream: stream.stream,
      abort: () {},
    );
  }

  @override
  Future<List<Map<String, dynamic>>> listPendingApprovals({
    required String projectId,
    required String sessionId,
  }) async {
    return const [];
  }

  @override
  Future<Map<String, dynamic>> projectChatTranscript({
    required String projectId,
    required String sessionId,
    int limit = 500,
    int? beforeSeq,
    int? afterSeq,
  }) async {
    transcriptCalls.add(afterSeq);
    final t = transcript;
    if (t == null) throw StateError('no fake transcript configured');
    final key = afterSeq == null ? null : afterSeq;
    return t[key] ?? t[null] ?? const {'blocks': <dynamic>[]};
  }

  @override
  Future<void> cancelAgentSession({
    required String projectId,
    required String sessionId,
  }) async {
    cancelCalls += 1;
    final err = cancelError;
    if (err != null) throw err;
  }
}

void main() {
  test('cancelStream surfaces api failure via error instead of throwing', () async {
    final boom = StateError('bridge down');
    final api = _FakeApi()..cancelError = boom;
    final controller = ChatSessionController(
      api: api,
      projectId: 'prj_cancel_err',
      sessionId: 'sess_1',
    );

    // Must not rethrow — an unhandled async error here used to crash the
    // composer cancel path.
    await controller.cancelStream();

    expect(api.cancelCalls, 1);
    expect(controller.error, same(boom));
    expect(controller.streaming, isFalse);
    controller.dispose();
  });

  test('cancelStream success keeps error null and marks stream stopped', () async {
    final api = _FakeApi();
    final controller = ChatSessionController(
      api: api,
      projectId: 'prj_cancel_ok',
      sessionId: 'sess_2',
    );

    await controller.cancelStream();

    expect(api.cancelCalls, 1);
    expect(controller.error, isNull);
    expect(controller.streaming, isFalse);
    controller.dispose();
  });

  test('cancelStream without session skips the api call', () async {
    final api = _FakeApi();
    final controller = ChatSessionController(
      api: api,
      projectId: 'prj_cancel_nosess',
      sessionId: '',
    );

    await controller.cancelStream();

    expect(api.cancelCalls, 0);
    expect(controller.error, isNull);
    controller.dispose();
  });

  group('showWorkingIndicator', () {
    test('true right after send (before first event), false while text streams', () async {
      final events = StreamController<Map<String, dynamic>>();
      final api = _FakeApi(stream: events);
      final controller = ChatSessionController(
        api: api,
        projectId: 'prj_indicator',
        sessionId: 'sess_ind_1',
      );

      final sendFuture = controller.send('hello');
      await pumpEventQueue();

      // Right after send: no event yet, nothing streaming, no tool call.
      expect(controller.streaming, isTrue);
      expect(controller.showWorkingIndicator, isTrue);

      // Text starts streaming → assistant block isStreaming → indicator off.
      events.add({'type': 'text_delta', 'data': {'text': 'wo'}});
      await pumpEventQueue();
      expect(controller.anyBlockStreaming, isTrue);
      expect(controller.showWorkingIndicator, isFalse);

      // Tool call without a result → pending → indicator off.
      events.add({'type': 'tool_call', 'data': {'id': 't1', 'name': 'Read', 'input': {}}});
      await pumpEventQueue();
      expect(controller.hasPendingToolCall, isTrue);
      expect(controller.showWorkingIndicator, isFalse);

      // Result arrives, nothing streams → between-events silence → indicator on.
      events.add({'type': 'tool_result', 'data': {'id': 't1', 'name': 'Read', 'output': 'x'}});
      await pumpEventQueue();
      expect(controller.hasPendingToolCall, isFalse);
      expect(controller.showWorkingIndicator, isTrue);

      await events.close();
      await sendFuture;
      await pumpEventQueue();

      // Turn finished → no indicator.
      expect(controller.streaming, isFalse);
      expect(controller.showWorkingIndicator, isFalse);
      controller.dispose();
    });

    test('usage events attach to the finished assistant block in visibleBlocks', () async {
      final events = StreamController<Map<String, dynamic>>();
      final api = _FakeApi(stream: events);
      final controller = ChatSessionController(
        api: api,
        projectId: 'prj_usage',
        sessionId: 'sess_usage_1',
      );

      final sendFuture = controller.send('hello');
      await pumpEventQueue();
      events.add({'type': 'text_delta', 'data': {'text': 'answer'}});
      events.add({'type': 'usage', 'data': {'input_tokens': 5, 'output_tokens': 6, 'model': 'm'}});
      await events.close();
      await sendFuture;
      await pumpEventQueue();

      expect(controller.visibleBlocks.where((b) => b.kind == 'usage'), isEmpty);
      final assistant = controller.visibleBlocks.lastWhere((b) => b.kind == 'assistant_markdown');
      expect((assistant.raw['usage'] as Map)['input_tokens'], 5);
      controller.dispose();
    });
  });

  group('usageCostUsd', () {
    test('computes from catalog prices by id and label', () {
      final controller = ChatSessionController(
        api: _FakeApi(),
        projectId: 'prj_cost',
        sessionId: 'sess_cost',
      )..availableModels = [
          {
            'id': 'model-a',
            'label': 'Model A',
            'input_price_usd_per_mtok': 3.0,
            'output_price_usd_per_mtok': 15.0,
          },
          {'id': 'model-b', 'label': 'Model B'},
        ];

      // (1000 / 1e6) * 3 + (2000 / 1e6) * 15 = 0.033
      expect(controller.usageCostUsd('model-a', 1000, 2000), closeTo(0.033, 1e-12));
      expect(controller.usageCostUsd('Model A', 100, 0), closeTo(0.0003, 1e-12));
      // No prices in the catalog entry → null.
      expect(controller.usageCostUsd('model-b', 1000, 1000), isNull);
      // Unknown model → null.
      expect(controller.usageCostUsd('nope', 1000, 1000), isNull);
      // No tokens to price → null.
      expect(controller.usageCostUsd('model-a', null, null), isNull);
      controller.dispose();
    });
  });

  group('reload mid-turn (turn_in_progress)', () {
    test('agentWorking reflects the server turn-in-progress flag after reload', () async {
      final api = _FakeApi()
        ..transcript = {
          null: {
            'session_id': 'sess_reload',
            'blocks': <dynamic>[],
            'turn_in_progress': true,
            'newest_seq': 7,
          },
        };
      final controller = ChatSessionController(
        api: api,
        projectId: 'prj_reload',
        sessionId: 'sess_reload',
      );

      expect(controller.agentWorking, isFalse);
      await controller.loadTranscript();

      // Reloaded client: not streaming locally, but the server says the agent
      // is working → the working indicator must show (not appear idle).
      expect(controller.streaming, isFalse);
      expect(controller.turnInProgress, isTrue);
      expect(controller.agentWorking, isTrue);
      expect(controller.showWorkingIndicator, isTrue);

      controller.dispose();
    });

    test('idle transcript reports agent not working', () async {
      final api = _FakeApi()
        ..transcript = {
          null: {
            'session_id': 'sess_idle',
            'blocks': <dynamic>[],
            'turn_in_progress': false,
          },
        };
      final controller = ChatSessionController(
        api: api,
        projectId: 'prj_idle',
        sessionId: 'sess_idle',
      );

      await controller.loadTranscript();

      expect(controller.turnInProgress, isFalse);
      expect(controller.agentWorking, isFalse);
      expect(controller.showWorkingIndicator, isFalse);
      controller.dispose();
    });
  });
}
