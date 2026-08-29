import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_module_host.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Hub page listing module tabs with `nav.placement: management`.
class CabinetManagementPage extends StatelessWidget {
  const CabinetManagementPage({
    super.key,
    required this.cabinetId,
    required this.entries,
    this.embedded = false,
  });

  final String cabinetId;
  final List<CabinetNavEntry> entries;
  final bool embedded;

  Future<void> _openModule(BuildContext context, CabinetNavEntry entry) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AppScaffold(
          title: Text(entry.label),
          body: CabinetModuleHost(
            cabinetId: cabinetId,
            entry: entry,
            embedded: true,
          ),
        ),
      ),
    );
  }

  Widget _listBody(AppLocalizations l10n) {
    if (entries.isEmpty) {
      return EmptyPlaceholder(title: l10n.navManagement);
    }

    return ListView.separated(
      padding: const EdgeInsets.all(AppSpacing.md),
      itemCount: entries.length,
      separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
      itemBuilder: (context, i) {
        final entry = entries[i];
        return AppListItem(
          leading: Icon(entry.icon),
          title: Text(entry.label),
          trailing: const AppTrailingChevron(),
          onTap: () => _openModule(context, entry),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);

    if (embedded) {
      return AppScaffold(body: _listBody(l10n));
    }

    return AppScaffold(
      title: Text(l10n.navManagement),
      body: _listBody(l10n),
    );
  }
}
