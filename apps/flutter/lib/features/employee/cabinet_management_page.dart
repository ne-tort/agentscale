import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_module_host.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Narrow-only hub: module tabs with `nav.placement: management` (from parent shell).
class CabinetManagementPage extends StatelessWidget {
  const CabinetManagementPage({
    super.key,
    required this.cabinetId,
    required this.entries,
  });

  final String cabinetId;
  final List<CabinetNavEntry> entries;

  Future<void> _open(BuildContext context, Widget page) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(builder: (_) => page),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);

    if (entries.isEmpty) {
      return AppScaffold(
        title: Text(l10n.navManagement),
        body: EmptyPlaceholder(title: l10n.navManagement),
      );
    }

    return AppScaffold(
      title: Text(l10n.navManagement),
      body: ListView.separated(
        padding: const EdgeInsets.all(AppSpacing.md),
        itemCount: entries.length,
        separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
        itemBuilder: (context, i) {
          final entry = entries[i];
          return AppListItem(
            leading: Icon(entry.icon),
            title: Text(entry.label),
            trailing: const AppTrailingChevron(),
            onTap: () => _open(
              context,
              CabinetModuleHost(cabinetId: cabinetId, entry: entry),
            ),
          );
        },
      ),
    );
  }
}
