import 'package:flutter/material.dart';

import 'package:prodavan/app.dart';
import 'package:prodavan/shell/app_state.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final appState = await createAppState();
  await appState.bootstrap();
  runApp(ProdavanApp(appState: appState));
}
