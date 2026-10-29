// Подгрузка истории не должна превращаться в бесконечный цикл.
// Инцидент: «есть чаты, после открытия которых страница зависает через пару
// секунд». Причина — автоподгрузка истории «пока контент не заполнит экран» без
// верхней границы. Если страница не добавляла видимых блоков и не двигала курсор,
// цикл `загрузили → не выросло → загрузили` шёл со скоростью сети, а список при
// этом ещё и перерисовывался целиком (eager ListView), поэтому UI замирал.
// Тесты фиксируют два рубежа защиты:
//   * контроллер — если страница не дала прогресса, листание прекращается;
//   * виджет — не больше N попыток подряд, дальше пауза до действия пользователя.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/chat_scaffold.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class _FakeApi extends Fake implements ProdavanApi {
  _FakeApi(this.pages);

  /// Ответы по beforeSeq (null = первая загрузка хвоста).
  final Map<int?, Map<String, dynamic>> pages;
  final List<int?> beforeCalls = [];

  @override
  Future<Map<String, dynamic>> projectChatTranscript({
    required String projectId,
    required String sessionId,
    int limit = 500,
    int? beforeSeq,
    int? afterSeq,
  }) async {
    beforeCalls.add(beforeSeq);
    return pages[beforeSeq] ?? const {'blocks': <dynamic>[]};
  }

  @override
  Future<List<Map<String, dynamic>>> listPendingApprovals({
    required String projectId,
    required String sessionId,
  }) async =>
      const [];
}

Map<String, dynamic> _page({
  required List<Map<String, dynamic>> blocks,
  required bool hasMore,
  int? oldestSeq,
  int? newestSeq,
}) =>
    {
      'session_id': 'sess_1',
      'blocks': blocks,
      'has_more': hasMore,
      'oldest_seq': oldestSeq,
      'newest_seq': newestSeq ?? 10,
      'total_events': 10,
      'pending_approvals': <Map<String, dynamic>>[],
      'turn_in_progress': false,
    };

Map<String, dynamic> _block(String key) => {
      'kind': 'assistant',
      'id': key,
      '_key': key,
      'text': 'x',
    };

void main() {
  test('страница без прогресса останавливает подгрузку истории', () async {
    // Хвост: 1 блок, есть ещё история (oldest_seq=5).
    final stuckPage = _page(
      blocks: [_block('a')],
      hasMore: true,
      oldestSeq: 5,
      newestSeq: 10,
    );
    // «Старшая» страница: пусто, курсор тот же, has_more всё ещё true —
    // именно так выглядел баг: сервер формально говорит «есть ещё», но
    // прогресса нет.
    final api = _FakeApi({
      null: stuckPage,
      5: _page(blocks: const [], hasMore: true, oldestSeq: 5, newestSeq: 10),
    });
    final controller = ChatSessionController(
      api: api,
      projectId: 'prj_loop',
      sessionId: 'sess_1',
    );

    await controller.loadTranscript();
    expect(controller.hasMoreHistory, isTrue);

    await controller.loadOlderTranscript();

    // Листать больше некуда — цикл разорван на первом же холостом шаге.
    expect(controller.hasMoreHistory, isFalse);
    expect(controller.oldestSeq, isNull);

    // И повторный вызов уже ничего не запрашивает.
    final callsAfterFirst = api.beforeCalls.length;
    await controller.loadOlderTranscript();
    expect(api.beforeCalls.length, callsAfterFirst);
  });

  test('bulk load pulls the whole history in one open', () async {
    final api = _FakeApi({
      null: _page(blocks: [_block('c')], hasMore: true, oldestSeq: 8, newestSeq: 10),
      8: _page(
        blocks: [_block('a'), _block('b')],
        hasMore: false,
        oldestSeq: 2,
        newestSeq: 10,
      ),
    });
    final controller = ChatSessionController(
      api: api,
      projectId: 'prj_ok',
      sessionId: 'sess_1',
    );

    await controller.loadTranscript();

    // The controller walks the older pages before notifying the UI once.
    expect(controller.blocks.length, 3);
    expect(controller.blocks.map((b) => b.text).toList(), ['x', 'x', 'x']);
    expect(controller.hasMoreHistory, isFalse);
    expect(controller.oldestSeq, isNull);

    // Nothing left to request.
    final calls = api.beforeCalls.length;
    await controller.loadOlderTranscript();
    expect(api.beforeCalls.length, calls);
  });

  test('пересекающийся диапазон не создаёт дублей', () async {
    // Бэкенд отдал уже известный блок вместе с новым — старый не дублируется,
    // иначе список растёт и сравнение «выросло ли» врёт.
    final api = _FakeApi({
      null: _page(blocks: [_block('b')], hasMore: true, oldestSeq: 8, newestSeq: 10),
      8: _page(
        blocks: [_block('a'), _block('b')],
        hasMore: false,
        oldestSeq: 1,
        newestSeq: 10,
      ),
    });
    final controller = ChatSessionController(
      api: api,
      projectId: 'prj_dup',
      sessionId: 'sess_1',
    );

    await controller.loadTranscript();
    await controller.loadOlderTranscript();

    expect(controller.blocks.map((b) => b.key).toList(), ['a', 'b']);
  });

  testWidgets('автоподгрузка прекращается после нескольких холостых попыток',
      (tester) async {
    var loadCalls = 0;
    // Виджет просит историю сам, пока контент не заполнит вьюпорт.
    // Раньше это был бесконечный цикл; теперь — не больше трёх попыток подряд.
    final blocks = <ChatBlock>[ChatBlock.fromJson(_block('a'))];

    await tester.pumpWidget(
      MaterialApp(
        locale: const Locale('ru'),
        supportedLocales: AppLocalizations.supportedLocales,
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        home: Scaffold(
          body: ChatMessageList(
            blocks: blocks,
            hasMoreHistory: true,
            loadingHistory: false,
            onLoadOlder: () => loadCalls++,
            turnStreaming: false,
            projectId: 'prj_w',
            sessionId: 'sess_1',
            api: _FakeApi(const {}),
            showWorkingIndicator: false,
            showReconnectIndicator: false,
            reconnectAttempt: null,
            reconnectMaxAttempts: null,
            reconnectNextModel: null,
            costResolver: null,
            mcpAliases: const {},
            onResolveApproval: null,
          ),
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(loadCalls, greaterThan(0), reason: 'первая попытка должна быть');
    expect(
      loadCalls,
      lessThanOrEqualTo(3),
      reason: 'цикл автоподгрузки обязан остановиться',
    );

    // Никаких запланированных задач не осталось — кадры не крутятся вечно.
    await tester.pump(const Duration(seconds: 1));
    expect(loadCalls, lessThanOrEqualTo(3));
  });
}

