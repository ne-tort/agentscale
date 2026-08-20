import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/auth/presentation/screens/placeholder_home_screen.dart';

class ProdavanApp extends StatelessWidget {
  const ProdavanApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Prodavan',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.system,
      home: const PlaceholderHomeScreen(),
    );
  }
}
