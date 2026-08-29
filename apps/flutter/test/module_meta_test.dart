import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_json_editor_field.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_meta_validator.dart';
import 'package:prodavan/features/meta/preview/module_meta_preview_page.dart';
import 'package:prodavan/features/meta/preview/seed_data_controller.dart';
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

    test('rejects seed_rows unknown table', () {
      final json = Map<String, dynamic>.from(suppliersManifestJson());
      json['seed_rows'] = {
        'items': [
          {'table_slug': 'missing', 'row_id': 'seed_1', 'body': {}},
        ],
      };
      final err = ModuleMetaValidator.validate(json);
      expect(err, contains('seed_rows'));
    });

    test('accepts employee nav.contour with placement', () {
      final json = Map<String, dynamic>.from(suppliersManifestJson());
      json['tabs'] = [
        {
          'id': 'tab_ok',
          'title': 'OK',
          'view_slug': 'suppliers_list',
          'nav': {'contour': 'employee', 'placement': 'management'},
        },
      ];
      expect(ModuleMetaValidator.validate(json), isNull);
    });

    test('rejects invalid nav.placement', () {
      final json = Map<String, dynamic>.from(suppliersManifestJson());
      json['tabs'] = [
        {
          'id': 'tab_bad',
          'title': 'Bad',
          'view_slug': 'suppliers_list',
          'nav': {'contour': 'employee', 'placement': 'sidebar'},
        },
      ];
      final err = ModuleMetaValidator.validate(json);
      expect(err, contains('nav.placement'));
    });

    test('rejects invalid nav.contour', () {
      final json = Map<String, dynamic>.from(suppliersManifestJson());
      json['tabs'] = [
        {
          'id': 'tab_bad',
          'title': 'Bad',
          'view_slug': 'suppliers_list',
          'nav': {'contour': 'unknown'},
        },
      ];
      final err = ModuleMetaValidator.validate(json);
      expect(err, contains('nav.contour'));
    });
  });

  group('ModuleMetaManifest', () {
    test('toSlugMap roundtrip includes seed_rows', () {
      final json = Map<String, dynamic>.from(suppliersManifestJson());
      json['seed_rows'] = {
        'items': [
          {
            'table_slug': 'suppliers',
            'row_id': 'seed_alpha',
            'body': {'name': 'Alpha', 'status': 'active'},
          },
        ],
      };
      final original = ModuleMetaManifest.fromJson(json);
      expect(original.seedRows, hasLength(1));
      final slugMap = original.toSlugMap();
      expect(slugMap[ModuleMetaSlugs.seedRows], isA<Map>());
      final restored = ModuleMetaManifest.fromSlugMap(slugMap);
      expect(restored.seedRows.first['row_id'], 'seed_alpha');
      expect(restored.tables, original.tables);
    });

    test('hasContent and isNonEmptyStubText', () {
      expect(ModuleMetaManifest.empty().hasContent, isFalse);
      expect(
        ModuleMetaManifest.fromJson(suppliersManifestJson()).hasContent,
        isTrue,
      );
      expect(
        ModuleMetaManifest.isNonEmptyStubText(
          ModuleMetaManifest.empty().toPrettyJson(),
        ),
        isFalse,
      );
      expect(
        ModuleMetaManifest.isNonEmptyStubText(
          const JsonEncoder.withIndent('  ').convert(suppliersManifestJson()),
        ),
        isTrue,
      );
    });

    test('enabledShellNavTabs filters by contour', () {
      final json = Map<String, dynamic>.from(suppliersManifestJson());
      json['tabs'] = [
        {
          'id': 'tab_cabinet',
          'title': 'Cabinet only',
          'order': 10,
          'view_slug': 'suppliers_list',
          'enabled': true,
        },
        {
          'id': 'tab_admin',
          'title': 'Admin nav',
          'order': 150,
          'view_slug': 'suppliers_list',
          'enabled': true,
          'nav': {'contour': 'admin'},
        },
        {
          'id': 'tab_company',
          'title': 'Company nav',
          'order': 160,
          'view_slug': 'suppliers_list',
          'enabled': true,
          'nav': {'contour': 'company'},
        },
      ];
      final manifest = ModuleMetaManifest.fromJson(json);
      expect(manifest.enabledTabs(), hasLength(3));
      expect(manifest.enabledShellNavTabs('admin'), hasLength(1));
      expect(manifest.enabledShellNavTabs('admin').first['title'], 'Admin nav');
      expect(manifest.enabledShellNavTabs('company'), hasLength(1));
    });

    test('mergeShellNavEntries dedupes title collisions', () {
      final entries = mergeShellNavEntries(
        contour: ShellNavContour.admin,
        modules: [
          (
            id: 'mod_a',
            name: 'Mod A',
            tabs: [
              {
                'title': 'Reports',
                'order': 1,
                'view_slug': 'suppliers_list',
                'nav': {'contour': 'admin'},
              },
            ],
          ),
          (
            id: 'mod_b',
            name: 'Mod B',
            tabs: [
              {
                'title': 'Reports',
                'order': 2,
                'view_slug': 'suppliers_list',
                'nav': {'contour': 'admin'},
              },
            ],
          ),
        ],
      );
      expect(entries, hasLength(2));
      expect(entries.map((e) => e.label), contains('Reports · Mod A'));
      expect(entries.map((e) => e.label), contains('Reports · Mod B'));
    });
  });

  group('SeedDataController', () {
    test('createRow applies column defaults', () {
      final manifest = ModuleMetaManifest.fromJson(suppliersManifestJson());
      final seeds = SeedDataController(manifest);
      final id = seeds.createRow('suppliers');
      expect(id, startsWith('seed_'));
      final body = seeds.bodyFor(id);
      expect(body['status'], 'active');
      expect(seeds.manifestWithSeed.seedRows, hasLength(1));
    });

    test('delete and patch', () {
      final manifest = ModuleMetaManifest.fromJson(suppliersManifestJson());
      final seeds = SeedDataController(manifest);
      final id = seeds.createRow('suppliers');
      seeds.patchField(id, 'name', 'Acme');
      expect(seeds.bodyFor(id)['name'], 'Acme');
      seeds.deleteRow(id);
      expect(seeds.itemsForTable('suppliers'), isEmpty);
    });
  });

  group('ModuleMetaPreviewPage', () {
    testWidgets('shows empty seed state then create adds row', (tester) async {
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
      expect(find.text('Нет предзаполненных строк'), findsOneWidget);
      expect(find.text('Предзаполнение'), findsWidgets);

      await tester.tap(find.byIcon(Icons.add));
      await tester.pumpAndSettle();
      // Form opens after create (row_tap open_form)
      expect(find.text('Имя'), findsWidgets);
      expect(find.text('Статус'), findsWidgets);
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

    testWidgets('shell nav preview chips when nav.contour set', (tester) async {
      final json = Map<String, dynamic>.from(suppliersManifestJson());
      json['tabs'] = [
        {
          'id': 'tab_suppliers',
          'title': 'Поставщики',
          'order': 150,
          'view_slug': 'suppliers_list',
          'enabled': true,
          'nav': {'contour': 'admin'},
        },
      ];
      final manifest = ModuleMetaManifest.fromJson(json);
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
      expect(find.text('Навигация shell (предпросмотр)'), findsOneWidget);
      await tester.tap(find.text('Поставщики').last);
      await tester.pumpAndSettle();
      expect(find.text('Alpha'), findsNothing);
    });
  });

  group('AppJsonEditorField', () {
    testWidgets('invalid JSON is not valid', (tester) async {
      final controller = TextEditingController(text: '{ invalid');
      addTearDown(controller.dispose);

      await tester.pumpWidget(
        _ruApp(
          Scaffold(
            body: AppJsonEditorField(
              controller: controller,
              validator: ModuleMetaValidator.validate,
            ),
          ),
        ),
      );
      await tester.pump();
      final field = tester.state<AppJsonEditorFieldState>(
        find.byType(AppJsonEditorField),
      );
      expect(field.isValidJson, isFalse);
    });

    testWidgets('valid JSON passes validation', (tester) async {
      final controller = TextEditingController(
        text: const JsonEncoder.withIndent('  ').convert(suppliersManifestJson()),
      );
      addTearDown(controller.dispose);

      await tester.pumpWidget(
        _ruApp(
          Scaffold(
            body: AppJsonEditorField(
              controller: controller,
              validator: ModuleMetaValidator.validate,
            ),
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

  group('AdminModuleJsonPage preview nav', () {
    testWidgets('shows preview row for non-empty manifest', (tester) async {
      await tester.pumpWidget(
        _ruApp(
          Scaffold(
            body: Builder(
              builder: (context) {
                final l10n = AppLocalizations.of(context);
                return Column(
                  children: [
                    if (ModuleMetaManifest.isNonEmptyStubText(
                      const JsonEncoder.withIndent('  ').convert(suppliersManifestJson()),
                    ))
                      AppNavPreference(
                        title: l10n.adminModulePreview,
                        icon: Icons.visibility_outlined,
                        onTap: () {},
                      ),
                  ],
                );
              },
            ),
          ),
        ),
      );
      await tester.pump();
      expect(find.text('Предзаполнение'), findsOneWidget);
      expect(find.byType(AppNavPreference), findsOneWidget);
    });

    testWidgets('hides preview row for empty stub', (tester) async {
      await tester.pumpWidget(
        _ruApp(
          Scaffold(
            body: Builder(
              builder: (context) {
                final l10n = AppLocalizations.of(context);
                final show = ModuleMetaManifest.isNonEmptyStubText(
                  ModuleMetaManifest.empty().toPrettyJson(),
                );
                return Column(
                  children: [
                    if (show)
                      AppNavPreference(
                        title: l10n.adminModulePreview,
                        icon: Icons.visibility_outlined,
                        onTap: () {},
                      ),
                  ],
                );
              },
            ),
          ),
        ),
      );
      await tester.pump();
      expect(find.byType(AppNavPreference), findsNothing);
    });
  });
}
