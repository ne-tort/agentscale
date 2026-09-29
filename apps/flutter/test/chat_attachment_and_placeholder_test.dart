import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/chat/widgets/chat_empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _themed(Widget home) {
  return MaterialApp(
    locale: const Locale('ru'),
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: Scaffold(body: Center(child: home)),
  );
}

void main() {
  testWidgets('inline_table attachment expands into a markdown table', (tester) async {
    const md = '| № | Партномер |\n| --- | --- |\n| 1 | P12688-B21 |';
    await tester.pumpWidget(
      _themed(
        const UserMessageBlock(
          text: '',
          attachments: [
            {
              'filename': 'spec.xlsx',
              'kind': 'inline_table',
              'row_count': 1,
              'inline_markdown': md,
            }
          ],
        ),
      ),
    );
    await tester.pumpAndSettle();

    // Collapsed: the table body is not mounted yet.
    expect(find.byType(ChatMarkdownBody), findsNothing);

    await tester.tap(find.byType(ChatMutedLine));
    await tester.pumpAndSettle();

    expect(find.byType(ChatMarkdownBody), findsOneWidget);
    // GFM table renders as a Table with the real headers.
    expect(find.text('Партномер'), findsOneWidget);
    expect(find.text('P12688-B21'), findsOneWidget);
    expect(find.text('№'), findsOneWidget);
  });

  testWidgets('legacy inline_json spoiler keeps rendering JSON', (tester) async {
    await tester.pumpWidget(
      _themed(
        const UserMessageBlock(
          text: '',
          attachments: [
            {
              'filename': 'rows.csv',
              'kind': 'inline_json',
              'row_count': 1,
              'inline_json': [
                {'a': '1'}
              ],
            }
          ],
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byType(ChatMutedLine));
    await tester.pumpAndSettle();
    expect(find.byType(ChatCodePanel), findsOneWidget);
    expect(find.textContaining('"a"'), findsOneWidget);
  });

  testWidgets('empty chat placeholder: no text, three dots, quiet morph', (tester) async {
    await tester.pumpWidget(_themed(const ChatEmptyPlaceholder()));
    await tester.pumpAndSettle();

    // Textless placeholder.
    expect(find.byType(Text), findsNothing);

    // Three dots.
    final dots = find.byWidgetPredicate(
      (w) => w is Container && (w.constraints?.minWidth ?? 0) == 6.0,
    );
    expect(dots, findsNWidgets(3));

    // Highlighted (drag hover): primary-tinted border and a subtle scale.
    await tester.pumpWidget(_themed(const ChatEmptyPlaceholder(highlighted: true)));
    await tester.pumpAndSettle();
    expect(find.byType(AnimatedScale), findsOneWidget);
    final scale = tester.widget<AnimatedScale>(find.byType(AnimatedScale));
    expect(scale.scale, 1.03);
  });
}
