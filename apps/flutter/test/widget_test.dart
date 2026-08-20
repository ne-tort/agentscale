import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:prodavan/app.dart';
import 'package:prodavan/shell/app_state.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('shows login screen when not authenticated', (WidgetTester tester) async {
    final appState = await createAppState();
    await appState.bootstrap();
    addTearDown(appState.dispose);

    await tester.pumpWidget(ProdavanApp(appState: appState));
    await tester.pump();

    expect(find.text('Prodavan'), findsWidgets);
    expect(find.text('Войти'), findsOneWidget);
  });
}
