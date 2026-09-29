import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _wrap(Size size, Widget child) {
  return MediaQuery(
    data: MediaQueryData(size: size),
    child: MaterialApp(
      locale: const Locale('en'),
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: child,
    ),
  );
}

AppLayout _layout() {
  return AppLayout(
    constrainBody: false,
    selectedIndex: 0,
    onDestinationSelected: (_) {},
    destinations: const [
      AppNavDestination(icon: Icons.dashboard_outlined, label: 'Overview'),
    ],
    body: const SizedBox.shrink(),
  );
}

void main() {
  testWidgets('logo shows the brand image and AgentScale label (extended rail)',
      (tester) async {
    await tester.binding.setSurfaceSize(const Size(1400, 800));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(_wrap(const Size(1400, 800), _layout()));
    await tester.pumpAndSettle();

    final image = tester.widget<Image>(find.byType(Image));
    expect(image.image, isA<AssetImage>());
    expect((image.image as AssetImage).assetName, 'assets/brand/agentscale.png');

    expect(find.text('AgentScale'), findsOneWidget);
    expect(find.text('Prodavan'), findsNothing);
    expect(find.text('AI'), findsNothing);
  });

  testWidgets('logo keeps the brand image in the collapsed rail', (tester) async {
    await tester.binding.setSurfaceSize(const Size(800, 600));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(_wrap(const Size(800, 600), _layout()));
    await tester.pumpAndSettle();

    final image = tester.widget<Image>(find.byType(Image));
    expect((image.image as AssetImage).assetName, 'assets/brand/agentscale.png');
    expect(find.text('AgentScale'), findsOneWidget);
    expect(find.text('Prodavan'), findsNothing);
    expect(find.text('AI'), findsNothing);
  });
}
