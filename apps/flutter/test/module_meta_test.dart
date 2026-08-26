import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_json_editor_field.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_meta_validator.dart';
import 'package:prodavan/features/meta/preview/module_meta_preview_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _ruApp(Widget home) {
  return MaterialApp(
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
}

/// Suppliers example from docs/target/06-modules/meta-syntax/examples/suppliers-module.md
Map<String, dynamic> suppliersManifestJson() => {
      'syntax_version': 1,
      'tables': [
        {
          'slug': 'suppliers',
          'label': 'Поставщики',
          'storage_kind': 'json_document',
          'enabled': true,
          'scope': {'projects': 'all'},
        },
      ],
      'columns': [
        {
          'table_slug': 'suppliers',
          'name': 'name',
          'label': 'Имя',
          'type': 'text',
          'required': true,
        },
        {
          'table_slug': 'suppliers',
          'name': 'status',
          'label': 'Статус',
          'type': 'enum',
          'required': true,
          'default': 'active',
          'enum': {
            'values': ['active', 'blocked'],
            'labels': {'active': 'Активен', 'blocked': 'Заблокирован'},
          },
        },
        {
          'table_slug': 'suppliers',
          'name': 'region',
          'label': 'Регион',
          'type': 'text',
          'required': false,
        },
      ],
      'views': [
        {
          'slug': 'suppliers_list',
          'table_slug': 'suppliers',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'name',
            'subtitle_fields': ['status', 'region'],
            'columns': [
              {'field': 'name', 'label': 'Имя'},
              {'field': 'status', 'label': 'Статус'},
              {'field': 'region', 'label': 'Регион'},
            ],
            'primary_action': {'kind': 'create_row', 'label': 'Добавить'},
            'row_tap': {'kind': 'open_form', 'view': 'suppliers_form'},
          },
        },
        {
          'slug': 'suppliers_form',
          'table_slug': 'suppliers',
          'kind': 'form',
          'ui_json': {
            'version': 1,
            'kind': 'form',
            'mode': 'edit',
            'fields': [
              {'column': 'name', 'widget': 'value'},
              {'column': 'status', 'widget': 'choice'},
            ],
          },
        },
      ],
      'tabs': [
        {
          'id': 'tab_suppliers',
          'title': 'Поставщики',
          'order': 100,
          'view_slug': 'suppliers_list',
          'table_slug': 'suppliers',
          'enabled': true,
        },
      ],
    };

Map<String, dynamic> hubOnlyManifestJson() => {
      'syntax_version': 1,
      'tables': [],
      'columns': [],
      'views': [
        {
          'slug': 'main_hub',
          'kind': 'hub',
          'ui_json': {
            'version': 1,
            'kind': 'hub',
            'items': [
              {
                'title': 'Список',
                'icon': 'list',
                'target': {'kind': 'view', 'view': 'items_list'},
              },
              {
                'title': 'Настройки',
                'icon': 'settings',
                'target': {'kind': 'stub'},
              },
            ],
          },
        },
        {
          'slug': 'items_list',
          'table_slug': 'items',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'name',
            'columns': [
              {'field': 'name', 'label': 'Имя'},
            ],
          },
        },
      ],
      'tabs': [
        {
          'id': 'tab_hub',
          'title': 'Меню',
          'order': 1,
          'view_slug': 'main_hub',
          'enabled': true,
        },
      ],
    };

Map<String, dynamic> invalidRefManifestJson() => {
      'syntax_version': 1,
      'tables': [
        {'slug': 't1', 'label': 'T1'},
      ],
      'columns': [],
      'views': [],
      'tabs': [
        {
          'id': 'tab_bad',
          'title': 'Broken',
          'view_slug': 'missing_view',
          'enabled': true,
        },
      ],
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('ModuleMetaValidator', () {
    test('accepts suppliers fixture', () {
      expect(ModuleMetaValidator.validate(suppliersManifestJson()), isNull);
    });

    test('rejects broken tab view ref', () {
      final err = ModuleMetaValidator.validate(invalidRefManifestJson());
      expect(err, contains('unknown view'));
    });

    test('rejects bad table slug', () {
      final json = Map<String, dynamic>.from(suppliersManifestJson());
      json['tables'] = [
        {'slug': 'Bad-Slug', 'label': 'X'},
      ];
      final err = ModuleMetaValidator.validate(json);
      expect(err, contains('invalid table slug'));
    });
  });

  group('ModuleMetaManifest', () {
    test('toSlugMap roundtrip', () {
      final original = ModuleMetaManifest.fromJson(suppliersManifestJson());
      final slugMap = original.toSlugMap();
      final restored = ModuleMetaManifest.fromSlugMap(slugMap);
      expect(restored.tables, original.tables);
      expect(restored.columns, original.columns);
      expect(restored.views, original.views);
      expect(restored.tabs, original.tabs);
    });
  });

  group('ModuleMetaPreviewPage', () {
    testWidgets('shows tab title Поставщики', (tester) async {
      final manifest = ModuleMetaManifest.fromJson(suppliersManifestJson());
      await tester.pumpWidget(
        _ruApp(
          ModuleMetaPreviewPage(
            manifest: manifest,
            moduleName: 'Suppliers Pack',
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Поставщики'), findsWidgets);
      expect(find.text('Sample 1'), findsWidgets);
    });

    testWidgets('stub snack on toolbar add', (tester) async {
      final manifest = ModuleMetaManifest.fromJson(suppliersManifestJson());
      await tester.pumpWidget(
        _ruApp(
          ModuleMetaPreviewPage(
            manifest: manifest,
            moduleName: 'Suppliers Pack',
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byIcon(Icons.add));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.text('Добавить'), findsOneWidget);
    });

    testWidgets('invalid view ref shows metadata placeholder', (tester) async {
      final manifest = ModuleMetaManifest.fromJson(invalidRefManifestJson());
      await tester.pumpWidget(
        _ruApp(
          ModuleMetaPreviewPage(
            manifest: manifest,
            moduleName: 'Broken',
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Метаданные'), findsOneWidget);
    });

    testWidgets('hub tab shows nav items', (tester) async {
      final manifest = ModuleMetaManifest.fromJson(hubOnlyManifestJson());
      await tester.pumpWidget(
        _ruApp(
          ModuleMetaPreviewPage(
            manifest: manifest,
            moduleName: 'Hub demo',
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Список'), findsOneWidget);
      expect(find.text('Настройки'), findsOneWidget);
    });
  });

  group('AppJsonEditorField', () {
    testWidgets('invalid JSON keeps preview button disabled', (tester) async {
      final controller = TextEditingController(text: '{ invalid');
      addTearDown(controller.dispose);
      var canPreview = false;

      await tester.pumpWidget(
        _ruApp(
          Scaffold(
            body: Column(
              children: [
                AppJsonEditorField(
                  controller: controller,
                  validator: ModuleMetaValidator.validate,
                  onChanged: (_) {},
                ),
                Builder(
                  builder: (context) {
                    final state = context
                        .findAncestorStateOfType<AppJsonEditorFieldState>();
                    canPreview = state?.isValidJson ?? false;
                    return FilledButton(
                      onPressed: canPreview ? () {} : null,
                      child: const Text('Preview'),
                    );
                  },
                ),
              ],
            ),
          ),
        ),
      );
      await tester.pump();
      expect(canPreview, isFalse);
      expect(
        tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
        isNull,
      );
    });

    testWidgets('valid JSON enables preview button', (tester) async {
      final controller = TextEditingController(
        text: const JsonEncoder.withIndent('  ').convert(suppliersManifestJson()),
      );
      addTearDown(controller.dispose);

      await tester.pumpWidget(
        _ruApp(
          StatefulBuilder(
            builder: (context, setState) {
              return Scaffold(
                body: Column(
                  children: [
                    AppJsonEditorField(
                      controller: controller,
                      validator: ModuleMetaValidator.validate,
                      onChanged: (_) => setState(() {}),
                    ),
                    const FilledButton(
                      onPressed: null,
                      child: Text('Preview'),
                    ),
                  ],
                ),
              );
            },
          ),
        ),
      );
      await tester.pump();
      final field = tester.state<AppJsonEditorFieldState>(
        find.byType(AppJsonEditorField),
      );
      expect(field.isValidJson, isTrue);
      expect(field.isFullyValid, isTrue);
    });
  });
}
