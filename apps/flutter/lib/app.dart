import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/dev_admin_session_page.dart';
import 'package:prodavan/features/company/dev_company_session_page.dart';
import 'package:prodavan/features/employee/dev_session_page.dart';
import 'package:prodavan/features/gallery/core_gallery_page.dart';

/// Platform entry — dev employee shell + L02 gallery.
class ProdavanApp extends StatelessWidget {
  const ProdavanApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Prodavan',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.system,
      home: const _HomePage(),
    );
  }
}

class _HomePage extends StatelessWidget {
  const _HomePage();

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
            Text('Prodavan dev shells', style: theme.textTheme.headlineSmall),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'Employee or Platform Admin bearer token → API',
              style: theme.textTheme.bodyMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: AppSpacing.lg),
            AppButton(
              label: 'Employee (dev)',
              expanded: false,
              onPressed: () {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const DevSessionPage()),
                );
              },
            ),
            const SizedBox(height: AppSpacing.sm),
            AppButton(
              label: 'Platform Admin (dev)',
              expanded: false,
              variant: AppButtonVariant.outlined,
              onPressed: () {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const DevAdminSessionPage()),
                );
              },
            ),
            const SizedBox(height: AppSpacing.sm),
            AppButton(
              label: 'Company admin (dev)',
              expanded: false,
              variant: AppButtonVariant.outlined,
              onPressed: () {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const DevCompanySessionPage()),
                );
              },
            ),
            const SizedBox(height: AppSpacing.sm),
            AppButton(
              label: 'UI Gallery',
              expanded: false,
              onPressed: () {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const CoreGalleryPage()),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}
