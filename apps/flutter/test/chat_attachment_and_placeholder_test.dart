import 'dart:typed_data';

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

  testWidgets('image attachment renders a thumbnail', (tester) async {
    // 1x1 PNG
    final png = Uint8List.fromList([
      0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D,
      0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
      0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC8, 0x89, 0x00, 0x00, 0x00,
      0x0A, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
    ]);
    final requested = <String>[];
    await tester.pumpWidget(
      _themed(
        UserMessageBlock(
          text: 'что на скриншоте?',
          attachmentRefs: const ['att_9'],
          attachments: const [
            {
              'filename': 'скрин.png',
              'storage_ref': 'object://p/inbox/скрин.png',
              'kind': 'image',
              'note': 'изображение передано модели; копия в контейнере',
              'workspace_path': 'inbox/скрин.png',
              'mime': 'image/png',
              'image_inline': true,
            },
          ],
          imageLoader: (id) async {
            requested.add(id);
            return png;
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(requested, ['att_9']);
    expect(find.byType(Image), findsOneWidget);
  });

  testWidgets('image attachment falls back to a label on load error', (tester) async {
    await tester.pumpWidget(
      _themed(
        UserMessageBlock(
          text: '',
          attachmentRefs: const ['att_gone'],
          attachments: const [
            {'filename': 'gone.png', 'kind': 'image'},
          ],
          imageLoader: (id) async => throw StateError('404'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byType(Image), findsNothing);
    expect(find.textContaining('gone.png'), findsOneWidget);
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
