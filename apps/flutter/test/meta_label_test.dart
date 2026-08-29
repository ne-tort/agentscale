import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

AppLocalizations _ruL10n() {
  return lookupAppLocalizations(const Locale('ru'));
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('resolveMetaLabel', () {
    test('plain string', () {
      expect(resolveMetaLabel('Добавить файл', _ruL10n()), 'Добавить файл');
    });

    test('locale map picks ru', () {
      expect(
        resolveMetaLabel(
          {'ru': 'Добавить файл', 'en': 'Add file'},
          _ruL10n(),
          locale: const Locale('ru'),
        ),
        'Добавить файл',
      );
    });

    test('locale map picks en', () {
      expect(
        resolveMetaLabel(
          {'ru': 'Добавить файл', 'en': 'Add file'},
          lookupAppLocalizations(const Locale('en')),
          locale: const Locale('en'),
        ),
        'Add file',
      );
    });

    test('l10n ref metaAddNew with args', () {
      expect(
        resolveMetaLabel(
          {'l10n': 'metaAddNew', 'args': {'item': 'MCP'}},
          _ruL10n(),
        ),
        'Добавить новый MCP',
      );
    });

    test('l10n ref companyAddEmployee', () {
      expect(
        resolveMetaLabel({'l10n': 'companyAddEmployee'}, _ruL10n()),
        'Добавить сотрудника',
      );
    });
  });

  group('resolveViewScaffoldTitle', () {
    test('returns form ui_json.title', () {
      final title = resolveViewScaffoldTitle(
        {
          'ui_json': {
            'kind': 'form',
            'title': 'MCP package',
          },
        },
        _ruL10n(),
      );
      expect(title, 'MCP package');
    });

    test('returns scaffold.title when set', () {
      final title = resolveViewScaffoldTitle(
        {
          'ui_json': {
            'kind': 'collection',
            'scaffold': {'title': {'ru': 'Заголовок'}},
          },
        },
        _ruL10n(),
        locale: const Locale('ru'),
      );
      expect(title, 'Заголовок');
    });

    test('returns null for collection without scaffold title', () {
      expect(
        resolveViewScaffoldTitle(
          {'ui_json': {'kind': 'collection'}},
          _ruL10n(),
        ),
        isNull,
      );
    });
  });
}
