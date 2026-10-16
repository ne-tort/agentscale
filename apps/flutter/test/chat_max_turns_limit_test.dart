import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/project_chat_settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _themed(Widget home) => MaterialApp(
      theme: AppTheme.light,
      locale: const Locale('ru'),
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: home,
    );

Widget _settingsPage() => Scaffold(
      body: ProjectChatSettingsPage(
        projectId: 'p1',
        sessionId: 's1',
        title: 'T',
        pinned: false,
        onTitleChanged: (_) {},
        onPinnedChanged: (_) {},
      ),
    );

void main() {
  group('настройка лимита шагов в чате', () {
    testWidgets('по умолчанию ограничение выключено, поле числа скрыто', (
      tester,
    ) async {
      await tester.pumpWidget(_themed(_settingsPage()));
      await tester.pumpAndSettle();

      expect(find.text('Ограничить количество шагов за раз'), findsOneWidget);
      // «Без ограничений» — числовое поле не показываем, пока лимит не включён
      expect(find.text('Шагов за раз'), findsNothing);
      expect(find.byIcon(Icons.format_list_numbered), findsOneWidget);
    });
  });

  group('уведомление о лимите шагов', () {
    test('done с reason=max_turns добавляет system_notice', () {
      final out = applyStreamEvent(const <ChatBlock>[], {
        'type': 'done',
        'data': {'reason': 'max_turns', 'turns': 25},
      });

      final notices = out.where((b) => b.kind == 'system_notice').toList();
      expect(notices, hasLength(1));
      expect(notices.single.raw['reason'], 'max_turns');
    });

    test('done с иной причиной system_notice не добавляет', () {
      final out = applyStreamEvent(const <ChatBlock>[], {
        'type': 'done',
        'data': {'reason': 'end_turn'},
      });

      expect(out.where((b) => b.kind == 'system_notice'), isEmpty);
    });

    test('done закрывает стриминг-блоки и без причины', () {
      final streaming = [
        ChatBlock(kind: 'assistant_markdown', raw: {
          'text': 'частичный ответ',
          '_streaming': true,
        }),
      ];
      final out = applyStreamEvent(streaming, {
        'type': 'done',
        'data': <String, dynamic>{},
      });

      expect(out.single.isStreaming, isFalse);
    });

    testWidgets('system_notice(max_turns) показывает подсказку', (tester) async {
      await tester.pumpWidget(
        _themed(
          Scaffold(
            body: ChatBlockRenderer(
              block: ChatBlock(
                kind: 'system_notice',
                raw: {'reason': 'max_turns'},
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        find.text('Лимит шагов закончен, попросите агента продолжить'),
        findsOneWidget,
      );
    });

    testWidgets('прочие system_notice остаются невидимыми', (tester) async {
      await tester.pumpWidget(
        _themed(
          Scaffold(
            body: ChatBlockRenderer(
              block: ChatBlock(
                kind: 'system_notice',
                raw: {'reason': 'compact_boundary'},
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        find.text('Лимит шагов закончен, попросите агента продолжить'),
        findsNothing,
      );
    });
  });
}
