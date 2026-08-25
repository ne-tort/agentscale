import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/session_gate_page.dart';

/// Platform entry — session restore → Sign in (OIDC or test personas).
class ProdavanApp extends StatelessWidget {
  const ProdavanApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Prodavan',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.system,
      home: const SessionGatePage(),
      debugShowCheckedModeBanner: kDebugMode,
    );
  }
}
