import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';

/// Platform UI stub. Implement product screens from docs/target/.
class ProdavanApp extends StatelessWidget {
  const ProdavanApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Prodavan',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.system,
      home: const _StubHomePage(),
    );
  }
}

class _StubHomePage extends StatelessWidget {
  const _StubHomePage();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return AppScaffold(
      title: const Text('Prodavan'),
      centerBody: true,
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Platform stub', style: theme.textTheme.headlineSmall),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'Implement from docs/target/',
              style: theme.textTheme.bodyMedium,
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}
