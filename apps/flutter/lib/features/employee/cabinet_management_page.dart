import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_module_host.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Hub listing module tabs for a shell placement (`management` or `data`).
///
/// Rows use the same preference-tile chrome as project settings.
/// Management/Data are scoped to the selected project instance (leaf copy).
class CabinetModuleHubPage extends StatelessWidget {
  const CabinetModuleHubPage({
    super.key,
    required this.cabinetId,
    required this.title,
    required this.entries,
    this.projectId,
    this.embedded = false,
    this.emptyIcon = Icons.apps_outlined,
  });

  final String cabinetId;
  final String? projectId;
  final String title;
  final List<CabinetNavEntry> entries;
  final bool embedded;
  final IconData emptyIcon;

  Future<void> _openModule(BuildContext context, CabinetNavEntry entry) {
    final pid = projectId;
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AppScaffold(
          title: Text(entry.label),
          body: CabinetModuleHost(
            cabinetId: cabinetId,
            projectId: pid,
            entry: entry,
            embedded: true,
          ),
        ),
      ),
    );
  }

  Widget _listBody(BuildContext context, AppLocalizations l10n) {
    final pid = projectId;
    if (pid == null || pid.isEmpty) {
      return EmptyPlaceholder(
        title: l10n.projectCreateProjectHint,
        icon: Icons.folder_outlined,
      );
    }
    if (entries.isEmpty) {
      return EmptyPlaceholder(
        title: l10n.companyNoModules,
        icon: emptyIcon,
      );
    }

    return ListView(
      padding: const EdgeInsets.all(AppSpacing.md),
      children: [
        for (final entry in entries)
          AppNavPreference(
            title: entry.label,
            icon: entry.icon,
            subtitle: entry.subtitle == null ? null : Text(entry.subtitle!),
            onTap: () => _openModule(context, entry),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);

    if (embedded) {
      return AppScaffold(body: _listBody(context, l10n));
    }

    return AppScaffold(
      title: Text(title),
      body: _listBody(context, l10n),
    );
  }
}

/// Management hub — tabs with `nav.placement: management`.
class CabinetManagementPage extends StatelessWidget {
  const CabinetManagementPage({
    super.key,
    required this.cabinetId,
    required this.entries,
    this.projectId,
    this.embedded = false,
  });

  final String cabinetId;
  final String? projectId;
  final List<CabinetNavEntry> entries;
  final bool embedded;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return CabinetModuleHubPage(
      cabinetId: cabinetId,
      projectId: projectId,
      title: l10n.navManagement,
      entries: entries,
      embedded: embedded,
      emptyIcon: Icons.apps_outlined,
    );
  }
}

/// Data hub — tabs with `nav.placement: data` (meta tables / meta syntax).
class CabinetDataPage extends StatelessWidget {
  const CabinetDataPage({
    super.key,
    required this.cabinetId,
    required this.entries,
    this.projectId,
    this.embedded = false,
  });

  final String cabinetId;
  final String? projectId;
  final List<CabinetNavEntry> entries;
  final bool embedded;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return CabinetModuleHubPage(
      cabinetId: cabinetId,
      projectId: projectId,
      title: l10n.navData,
      entries: entries,
      embedded: embedded,
      emptyIcon: Icons.table_chart_outlined,
    );
  }
}
