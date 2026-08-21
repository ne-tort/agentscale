import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/app.dart';

void main() {
  testWidgets('stub home renders', (tester) async {
    await tester.pumpWidget(const ProdavanApp());
    expect(find.text('Prodavan'), findsOneWidget);
    expect(find.text('Platform stub'), findsOneWidget);
  });
}
