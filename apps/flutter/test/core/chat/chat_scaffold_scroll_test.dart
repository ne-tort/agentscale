import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/chat/widgets/chat_scaffold.dart';
import 'package:prodavan/core/chat/widgets/chat_turn_list.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class _FakeApi extends Fake implements ProdavanApi {}

void main() {
  testWidgets('short turn inside the real scaffold sticks to the bottom', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final api = _FakeApi();
    final controller = ChatSessionController(
      api: api,
      projectId: 'p1',
      sessionId: 's1',
    );
    controller.blocks.addAll([
      ChatBlock(kind: 'user', raw: {'text': 'привет'}),
      ChatBlock(
        kind: 'assistant_markdown',
        raw: {'text': '<final_answer>короткий ответ</final_answer>'},
      ),
    ]);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        locale: const Locale('ru'),
        supportedLocales: AppLocalizations.supportedLocales,
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        home: Scaffold(
          body: ChatScaffold(
            controller: controller,
            api: api,
            chatSendable: true,
            loading: false,
            title: const Text('chat'),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final list = tester.getRect(find.byType(ChatTurnList));
    final answer = tester.getRect(find.byType(ChatMarkdownBody));
    final user = tester.getRect(find.text('привет'));
    debugPrint('SCAFFOLD LIST=$list ANSWER=$answer USER=$user');
    expect(answer.bottom, greaterThan(list.bottom - 100));
  });
}
