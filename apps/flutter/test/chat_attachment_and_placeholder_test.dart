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

  testWidgets('empty chat placeholder: top-anchored skeleton plates, no text', (tester) async {
    await tester.pumpWidget(_themed(const ChatEmptyPlaceholder()));
    await tester.pump();

    // A placeholder "picture": no text of any kind.
    expect(find.byType(Text), findsNothing);

    // Skeleton plates: circular avatar placeholder + card + lines + bubble.
    final circle = find.byWidgetPredicate(
      (w) => w is Container && (w.constraints?.minWidth ?? 0) == 34.0,
    );
    expect(circle, findsOneWidget);

    // Top-anchored: the avatar plate starts at the top-left padding (~16,16),
    // not centered in the viewport.
    final topLeft = tester.getTopLeft(circle);
    expect(topLeft.dx, moreOrLessEquals(16, epsilon: 1));
    expect(topLeft.dy, lessThan(100));

    // The dialog picture continues below the assistant card (lines + reply).
    expect(find.byType(FractionallySizedBox), findsNWidgets(4));
  });

}
