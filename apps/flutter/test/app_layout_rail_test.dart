import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/l10n/app_localizations.dart';

void main() {
  Widget wrap(Size size, Widget child) {
    return MediaQuery(
      data: MediaQueryData(size: size),
      child: MaterialApp(
        locale: const Locale('en'),
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: child,
      ),
    );
  }

  AppLayout layout() {
    return AppLayout(
      constrainBody: false,
      selectedIndex: 0,
      onDestinationSelected: (_) {},
      onOpenSettings: () {},
      destinations: const [
        AppNavDestination(icon: Icons.dashboard_outlined, label: 'Overview'),
        AppNavDestination(icon: Icons.business_outlined, label: 'Companies'),
      ],
      body: const ColoredBox(
        color: Color(0xFF00AA00),
        child: Center(child: Text('CONTENT_MARKER')),
      ),
    );
  }

  testWidgets('content visible with compact rail (medium)', (tester) async {
    await tester.binding.setSurfaceSize(const Size(800, 600));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(wrap(const Size(800, 600), layout()));
    await tester.pumpAndSettle();

    expect(find.text('CONTENT_MARKER'), findsOneWidget);
  });

  testWidgets('content visible with extended rail (expanded)', (tester) async {
    await tester.binding.setSurfaceSize(const Size(1400, 800));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(wrap(const Size(1400, 800), layout()));
    await tester.pumpAndSettle();

    expect(find.text('CONTENT_MARKER'), findsOneWidget);
    expect(find.text('Prodavan'), findsOneWidget);
  });

  testWidgets('content survives medium → expanded resize', (tester) async {
    await tester.binding.setSurfaceSize(const Size(900, 700));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(wrap(const Size(900, 700), layout()));
    await tester.pumpAndSettle();
    expect(find.text('CONTENT_MARKER'), findsOneWidget);

    await tester.binding.setSurfaceSize(const Size(1280, 800));
    await tester.pumpWidget(wrap(const Size(1280, 800), layout()));
    await tester.pumpAndSettle();

    expect(find.text('CONTENT_MARKER'), findsOneWidget);
  });
}
