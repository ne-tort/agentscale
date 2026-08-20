import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/app.dart';

void main() {
  testWidgets('shows Prodavan placeholder home', (WidgetTester tester) async {
    await tester.pumpWidget(const ProdavanApp());
    expect(find.text('Prodavan'), findsOneWidget);
    expect(find.text('I0 scaffold'), findsOneWidget);
  });
}
