import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/core/widgets/app_shell_branch.dart';
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

  AppLayout layout({bool subpageOpen = false, Widget? body}) {
    return AppLayout(
      constrainBody: false,
      subpageOpen: subpageOpen,
      selectedIndex: 0,
      onDestinationSelected: (_) {},
      destinations: const [
        AppNavDestination(icon: Icons.dashboard_outlined, label: 'Overview'),
        AppNavDestination(icon: Icons.business_outlined, label: 'Companies'),
      ],
      body: body ??
          const ColoredBox(
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

  testWidgets('settings pinned near bottom of rail', (tester) async {
    await tester.binding.setSurfaceSize(const Size(800, 600));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      wrap(
        const Size(800, 600),
        AppLayout(
          constrainBody: false,
          subpageOpen: false,
          selectedIndex: 0,
          onDestinationSelected: (_) {},
          trailingDestination: const AppNavDestination(
            icon: Icons.settings_outlined,
            label: 'Settings',
          ),
          onTrailingSelected: () {},
          destinations: const [
            AppNavDestination(icon: Icons.dashboard_outlined, label: 'Overview'),
            AppNavDestination(icon: Icons.business_outlined, label: 'Companies'),
          ],
          body: const ColoredBox(color: Color(0xFF00AA00)),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final railBox = tester.getRect(find.byType(NavigationRail));
    final settingsIcon = tester.getRect(find.byIcon(Icons.settings_outlined));
    expect(settingsIcon.bottom, greaterThan(railBox.center.dy));
    expect(settingsIcon.bottom, lessThanOrEqualTo(railBox.bottom + 1));
  });

  testWidgets('settings appears as bottom nav item when trailing on narrow', (tester) async {
    tester.view.physicalSize = const Size(390, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      wrap(
        const Size(390, 800),
        AppLayout(
          constrainBody: false,
          selectedIndex: 0,
          onDestinationSelected: (_) {},
          trailingDestination: const AppNavDestination(
            icon: Icons.settings_outlined,
            label: 'Settings',
          ),
          trailingSelected: false,
          onTrailingSelected: () {},
          destinations: const [
            AppNavDestination(icon: Icons.dashboard_outlined, label: 'Overview'),
          ],
          body: const ColoredBox(color: Color(0xFF00AA00)),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final bar = tester.widget<NavigationBar>(find.byType(NavigationBar));
    final labels = bar.destinations
        .map((d) => (d as NavigationDestination).label)
        .toList();
    expect(labels, ['Overview', 'Settings']);
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

  testWidgets('subpage keeps rail visible and uses compact labels at 1100px', (tester) async {
    await tester.binding.setSurfaceSize(const Size(1100, 700));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      wrap(
        const Size(1100, 700),
        layout(
          subpageOpen: true,
          body: AppShellBranch(
            active: true,
            root: Builder(
              builder: (context) => Scaffold(
                appBar: AppBar(title: const Text('Detail')),
                body: Center(
                  child: TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: const Text('POP'),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byType(NavigationRail), findsOneWidget);
    final rail = tester.widget<NavigationRail>(find.byType(NavigationRail));
    expect(rail.extended, isFalse);
  });

  testWidgets('subpage allows extended rail when significantly wide', (tester) async {
    await tester.binding.setSurfaceSize(const Size(1400, 800));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      wrap(
        const Size(1400, 800),
        layout(subpageOpen: true),
      ),
    );
    await tester.pumpAndSettle();

    final rail = tester.widget<NavigationRail>(find.byType(NavigationRail));
    expect(rail.extended, isTrue);
  });
}
