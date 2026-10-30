import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/meta/widgets/customer_positions_panel.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class _FakeApi extends Fake implements ProdavanApi {
  int invokes = 0;

  @override
  Future<List<Map<String, dynamic>>> listProjectRuntimeModuleDataRows({
    required String projectId,
    required String moduleId,
    required String tableSlug,
    String? sessionId,
    bool chatsAll = false,
  }) async {
    if (tableSlug == 'request_lines') {
      return [
        {
          'row_id': 'line1',
          'session_id': sessionId,
          'body': {
            'title': 'Сервер 2U',
            'part_number': 'SRV-1',
            'qty': 2,
            'selected_offer_id': 'offer1',
            'status': 'selected',
          },
        },
      ];
    }
    if (tableSlug == 'found_groups') return const [];
    if (tableSlug == 'found_offers') {
      return [
        {
          'row_id': 'offer1',
          'body': {
            'line_id': 'line1',
            'title': 'Сервер Dell R650',
            'part_number': 'DELL-R650',
            'seller': 'ООО Поставщик',
            'price': 1000000,
            'currency': 'RUB',
            'match_kind': 'exact',
            'is_selected': true,
            'is_effective': true,
            'in_stock': true,
          },
        },
        {
          'row_id': 'offer2',
          'body': {
            'line_id': 'line1',
            'title': 'Аналог HPE DL380',
            'part_number': 'HPE-DL380',
            'seller': 'ООО Аналог',
            'price': 900000,
            'currency': 'RUB',
            'match_kind': 'analog',
            'in_stock': true,
          },
        },
      ];
    }
    return const [];
  }

  @override
  Future<Map<String, dynamic>> invokeModuleAction({
    required String cabinetId,
    required String moduleId,
    required String actionId,
    String? rowId,
    String? projectId,
    String? sessionId,
    Map<String, dynamic>? params,
  }) async {
    invokes++;
    return const {};
  }
}

Widget _app(Widget child) => MaterialApp(
      theme: AppTheme.light,
      locale: const Locale('ru'),
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: Scaffold(body: child),
    );

void main() {
  testWidgets('customer positions renders offers, analog color and details',
      (tester) async {
    final api = _FakeApi();
    await tester.pumpWidget(
      _app(
        CustomerPositionsPanel(
          api: api,
          cabinetId: 'cab',
          projectId: 'proj',
          sessionId: 'sess',
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('Позиции заказчика'), findsOneWidget);
    expect(find.text('Сервер 2U'), findsOneWidget);
    expect(find.textContaining('1 000'), findsOneWidget);
    expect(find.text('Аналог'), findsOneWidget);

    // Tap the analog offer: selects it and expands part number/seller.
    await tester.tap(find.text('Аналог HPE DL380'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(api.invokes, 1);
    expect(find.textContaining('Партномер'), findsOneWidget);
    expect(find.text('Продавец: ООО Аналог'), findsOneWidget);
  });
}
