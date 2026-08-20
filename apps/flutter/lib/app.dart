import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/shell/app_root.dart';
import 'package:prodavan/shell/app_scope.dart';
import 'package:prodavan/shell/app_state.dart';

class ProdavanApp extends StatelessWidget {
  const ProdavanApp({super.key, required this.appState});

  final AppState appState;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Prodavan',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.system,
      home: AppScope(
        appState: appState,
        child: AppRoot(appState: appState),
      ),
    );
  }
}
