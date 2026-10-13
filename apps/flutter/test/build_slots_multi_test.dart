import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/meta/widgets/build_slots_field.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _ruApp(Widget home) => MaterialApp(
      theme: AppTheme.light,
      locale: const Locale('ru'),
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: Scaffold(body: home),
    );

class _FakeSeeds {
  _FakeSeeds(this._types, this._groups);
  final List<Map<String, dynamic>> _types;
  final Map<String, dynamic> _groups;

  List<dynamic> itemsForTable(String table) => table == 'equipment_types' ? _types : const [];
  Map<String, dynamic>? itemById(String id) => _groups[id];
}

void main() {
  testWidgets('несколько слотов одного типа — разные строки, qty по слоту', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(500, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final seeds = _FakeSeeds(
      [
        {'row_id': 'etype_storage', 'body': {'name': 'Накопитель'}},
      ],
      {
        'g_ssd': {
          'body': {'face_title': 'SSD 500GB', 'face_price': 4873.2, 'offers_count': 3},
        },
        'g_hdd': {
          'body': {'face_title': 'HDD 2TB', 'face_price': 2929.5, 'offers_count': 3},
        },
      },
    );

    await tester.pumpWidget(
      _ruApp(
        BuildSlotsField(
          seeds: seeds,
          // два слота типа etype_storage с разной идентичностью
          slots: const {'s_ssd': 'g_ssd', 's_hdd': 'g_hdd'},
          slotQty: const {'s_ssd': 1, 's_hdd': 2},
          slotTypes: const {'s_ssd': 'etype_storage', 's_hdd': 'etype_storage'},
          buildKind: 'pc',
          readOnly: false,
          typesTable: 'equipment_types',
          itemsTable: 'equipment_items',
          offersTable: 'found_offers',
          pickView: 'found_groups_pick',
          sectionTitle: 'Компоненты',
          emptyLabel: 'Не выбран',
          rowId: 'b1',
          onOpenPick: (_, {rowId}) {},
          onQtyChanged: (_, __) {},
        ),
      ),
    );
    await tester.pumpAndSettle();

    // строка типа + две дополнительные строки слотов
    expect(find.text('Накопитель'), findsWidgets);
    expect(find.textContaining('SSD 500GB'), findsOneWidget);
    expect(find.textContaining('HDD 2TB'), findsOneWidget);
    // у каждого слота свой счётчик (1 и 2), а не общий на тип
    expect(find.text('1'), findsOneWidget);
    expect(find.text('2'), findsOneWidget);
  });

  testWidgets('один слот на тип — ровно как раньше', (tester) async {
    tester.view.physicalSize = const Size(500, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final seeds = _FakeSeeds(
      [
        {'row_id': 'etype_cpu', 'body': {'name': 'Процессор'}},
      ],
      {
        'g_cpu': {
          'body': {'face_title': 'i5-12400F', 'face_price': 11532.0, 'offers_count': 3},
        },
      },
    );

    await tester.pumpWidget(
      _ruApp(
        BuildSlotsField(
          seeds: seeds,
          // ключ слота = тип (старая схема)
          slots: const {'etype_cpu': 'g_cpu'},
          slotQty: const {'etype_cpu': 1},
          slotTypes: const {},
          buildKind: 'pc',
          readOnly: false,
          typesTable: 'equipment_types',
          itemsTable: 'equipment_items',
          offersTable: 'found_offers',
          pickView: 'found_groups_pick',
          sectionTitle: 'Компоненты',
          emptyLabel: 'Не выбран',
          rowId: 'b1',
          onOpenPick: (_, {rowId}) {},
        ),
      ),
    );
    await tester.pumpAndSettle();

    // ровно одна строка процессора, никаких дублей
    expect(find.text('Процессор'), findsOneWidget);
    expect(find.textContaining('i5-12400F'), findsOneWidget);
  });
}
