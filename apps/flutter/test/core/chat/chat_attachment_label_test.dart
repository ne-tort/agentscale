import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/l10n/app_localizations.dart';
import 'package:prodavan/l10n/app_localizations_en.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

Widget themed(Widget home, {Locale locale = const Locale('en')}) {
  return MaterialApp(
    theme: AppTheme.light,
    locale: locale,
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: Scaffold(body: home),
  );
}

void main() {
  group('chatAttachmentRowsLabel plurals', () {
    test('ru: строка/строки/строк', () {
      final ru = AppLocalizationsRu();
      expect(ru.chatAttachmentRowsLabel('f', 1), 'Вложение: f (1 строка)');
      expect(ru.chatAttachmentRowsLabel('f', 2), 'Вложение: f (2 строки)');
      expect(ru.chatAttachmentRowsLabel('f', 5), 'Вложение: f (5 строк)');
      expect(ru.chatAttachmentRowsLabel('f', 21), 'Вложение: f (21 строка)');
      expect(ru.chatAttachmentRowsLabel('f', 111), 'Вложение: f (111 строк)');
    });

    test('en: row/rows', () {
      final en = AppLocalizationsEn();
      expect(en.chatAttachmentRowsLabel('f', 1), 'Attachment: f (1 row)');
      expect(en.chatAttachmentRowsLabel('f', 2), 'Attachment: f (2 rows)');
    });

    test('label variants are localized in both locales', () {
      final ru = AppLocalizationsRu();
      final en = AppLocalizationsEn();
      expect(ru.chatAttachmentLabel('f'), 'Вложение: f');
      expect(en.chatAttachmentLabel('f'), 'Attachment: f');
      expect(ru.chatAttachmentJsonLabel('f'), 'Вложение: f (JSON)');
      expect(en.chatAttachmentJsonLabel('f'), 'Attachment: f (JSON)');
      expect(
        ru.chatAttachmentPathLabel('f', 'in/f'),
        'Вложение: f → /workspace/in/f',
      );
      expect(
        en.chatAttachmentPathLabel('f', 'in/f'),
        'Attachment: f → /workspace/in/f',
      );
    });
  });

  group('UserMessageBlock attachment labels', () {
    testWidgets('ru inline_json uses correct plural forms', (tester) async {
      await tester.pumpWidget(
        themed(
          const UserMessageBlock(
            text: 'hi',
            attachments: [
              {'filename': 'data.json', 'kind': 'inline_json', 'row_count': 5},
              {'filename': 'one.json', 'kind': 'inline_json', 'row_count': 1},
            ],
          ),
          locale: const Locale('ru'),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Вложение: data.json (5 строк)'), findsOneWidget);
      expect(find.text('Вложение: one.json (1 строка)'), findsOneWidget);
    });

    testWidgets('en labels: rows, JSON fallback, workspace path', (tester) async {
      await tester.pumpWidget(
        themed(
          const UserMessageBlock(
            text: 'hi',
            attachments: [
              {'filename': 'data.json', 'kind': 'inline_json', 'row_count': 2},
              {'filename': 'raw.json', 'kind': 'inline_json'},
              {'filename': 'f.csv', 'workspace_path': 'in/f.csv'},
              {'filename': 'plain.txt'},
            ],
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Attachment: data.json (2 rows)'), findsOneWidget);
      expect(find.text('Attachment: raw.json (JSON)'), findsOneWidget);
      expect(find.text('Attachment: f.csv → /workspace/in/f.csv'), findsOneWidget);
      expect(find.text('Attachment: plain.txt'), findsOneWidget);
    });
  });
}
