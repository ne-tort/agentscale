import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';

class _FakeApi extends Fake implements ProdavanApi {
  Object? cancelError;
  int cancelCalls = 0;

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
}
