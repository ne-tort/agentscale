import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/cabinet_management_page.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/features/employee/cabinet_shell.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
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

void main() {
  group('cabinetNavPlacementOf', () {
    test('defaults to management when nav absent', () {
      expect(
        cabinetNavPlacementOf({'id': 't', 'title': 'T'}),
        CabinetNavPlacement.management,
      );
    });

    test('respects explicit rail placement', () {
      expect(
        cabinetNavPlacementOf({
          'id': 't',
          'nav': {'contour': 'employee', 'placement': 'rail'},
        }),
        CabinetNavPlacement.rail,
      );
    });

    test('respects explicit management placement', () {
      expect(
        cabinetNavPlacementOf({
          'id': 't',
          'nav': {'contour': 'cabinet', 'placement': 'management'},
        }),
        CabinetNavPlacement.management,
      );
    });

    test('excludes admin contour from cabinet shell', () {
      expect(
        cabinetNavPlacementOf({
          'id': 't',
          'nav': {'contour': 'admin', 'placement': 'rail'},
        }),
        CabinetNavPlacement.none,
      );
    });

    test('excludes company contour from cabinet shell', () {
      expect(
        cabinetNavPlacementOf({
          'id': 't',
          'nav': {'contour': 'company'},
        }),
        CabinetNavPlacement.none,
      );
    });

    test('respects explicit data placement', () {
      expect(
        cabinetNavPlacementOf({
          'id': 't',
          'nav': {'contour': 'employee', 'placement': 'data'},
        }),
        CabinetNavPlacement.data,
      );
    });

    test('none placement excludes tab from both rail and management', () {
      expect(
        cabinetNavPlacementOf({
          'id': 't',
          'nav': {'contour': 'employee', 'placement': 'none'},
        }),
        CabinetNavPlacement.none,
      );
    });

    test('employee contour without placement defaults to management', () {
      expect(
        cabinetNavPlacementOf({
          'id': 't',
          'nav': {'contour': 'employee'},
        }),
        CabinetNavPlacement.management,
      );
    });
  });

  group('moduleInstanceOwnerOf', () {
    test('defaults rail to cabinet', () {
      expect(
        moduleInstanceOwnerOf({
          'id': 't',
          'nav': {'contour': 'employee', 'placement': 'rail'},
        }),
        'cabinet',
      );
    });

    test('defaults management/data to project', () {
      expect(
        moduleInstanceOwnerOf({
          'id': 't',
          'nav': {'contour': 'employee', 'placement': 'management'},
        }),
        'project',
      );
      expect(
        moduleInstanceOwnerOf({
          'id': 't',
          'nav': {'contour': 'employee', 'placement': 'data'},
        }),
        'project',
      );
    });

    test('reads top-level instance_owner', () {
      expect(
        moduleInstanceOwnerOf({
          'id': 't',
          'instance_owner': 'cabinet',
          'nav': {'contour': 'employee', 'placement': 'management'},
        }),
        'cabinet',
      );
    });

    test('reads nav.instance_owner', () {
      expect(
        moduleInstanceOwnerOf({
          'id': 't',
          'nav': {
            'contour': 'employee',
            'placement': 'data',
            'instance_owner': 'cabinet',
          },
        }),
        'cabinet',
      );
    });

    test('isCabinetInstanceOwner mirrors owner', () {
      expect(
        isCabinetInstanceOwner({
          'instance_owner': 'cabinet',
          'nav': {'placement': 'management'},
        }),
        isTrue,
      );
      expect(
        isCabinetInstanceOwner({
          'nav': {'contour': 'employee', 'placement': 'management'},
        }),
        isFalse,
      );
    });
  });

  group('CabinetNavEntry.usesProjectLeaf', () {
    test('cabinet owner does not use project leaf', () {
      const entry = CabinetNavEntry(
        moduleId: 'mod_prompts',
        moduleName: 'Промпты',
        tab: {
          'title': 'Промпты',
          'view_slug': 'prompt_profiles_list',
          'instance_owner': 'cabinet',
          'nav': {'contour': 'employee', 'placement': 'management'},
        },
        label: 'Промпты',
      );
      expect(entry.instanceOwner, 'cabinet');
      expect(entry.usesProjectLeaf, isFalse);
    });
  });

  group('CabinetManagementPage', () {
    testWidgets('renders preference tiles with subtitle', (tester) async {
      const entry = CabinetNavEntry(
        moduleId: 'mod_mcp',
        moduleName: 'MCP',
        tab: {
          'id': 'tab_mcp',
          'title': 'MCP',
          'subtitle': 'Инструменты и интеграции',
          'icon': 'hub',
          'view_slug': 'mcp_packages_list',
          'instance_owner': 'cabinet',
        },
        label: 'MCP',
      );

      await tester.pumpWidget(
        _ruApp(
          const CabinetManagementPage(
            cabinetId: 'cab-1',
            projectId: 'prj-1',
            entries: [entry],
          ),
        ),
      );

      expect(find.text('MCP'), findsOneWidget);
      expect(find.text('Инструменты и интеграции'), findsOneWidget);
      expect(find.text('Проекты'), findsNothing);
    });

    testWidgets('shows cabinet-owned entries without projectId', (tester) async {
      const entry = CabinetNavEntry(
        moduleId: 'mod_mcp',
        moduleName: 'MCP',
        tab: {
          'id': 'tab_mcp',
          'title': 'MCP',
          'icon': 'hub',
          'view_slug': 'mcp_packages_list',
          'instance_owner': 'cabinet',
          'nav': {'contour': 'employee', 'placement': 'management'},
        },
        label: 'MCP',
      );

      await tester.pumpWidget(
        _ruApp(
          const CabinetManagementPage(
            cabinetId: 'cab-1',
            entries: [entry],
          ),
        ),
      );

      expect(find.text('MCP'), findsOneWidget);
      expect(find.byIcon(Icons.folder_outlined), findsNothing);
    });
  });

  group('CabinetShell destinations', () {
    testWidgets('wide rail has Projects; empty hubs stay hidden', (tester) async {
      tester.view.physicalSize = const Size(900, 700);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        _ruApp(
          const CabinetShell(cabinetId: 'cab-1', cabinetName: 'Test'),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));

      expect(find.text('Проекты'), findsWidgets);
      expect(find.text('Управление'), findsNothing);
      expect(find.text('Данные'), findsNothing);
    });

    testWidgets('narrow bottom nav shows Projects and Overview without empty hubs', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(400, 800);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        _ruApp(
          const CabinetShell(cabinetId: 'cab-1', cabinetName: 'Test'),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));

      expect(find.byType(NavigationBar), findsOneWidget);
      expect(find.text('Проекты'), findsOneWidget);
      expect(find.text('Обзор'), findsOneWidget);
      expect(find.text('Управление'), findsNothing);
      expect(find.text('Данные'), findsNothing);
    });
  });
}
