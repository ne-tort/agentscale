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
/// Global-bound tabs (`default_project_bind: global`) edit cabinet SoT without a
/// selected project; local-bound tabs need a project leaf.
class CabinetModuleHubPage extends StatelessWidget {
  const CabinetModuleHubPage({
    super.key,
    required this.cabinetId,
    required this.title,
    required this.entries,
    this.projectId,
    this.sessionId,
    this.embedded = false,
    this.emptyIcon = Icons.apps_outlined,
  });

  final String cabinetId;
  final String? projectId;
  final String? sessionId;
  final String title;
  final List<CabinetNavEntry> entries;
  final bool embedded;
  final IconData emptyIcon;

  Future<void> _openModule(BuildContext context, CabinetNavEntry entry) {
    // global bind → cabinet SoT (projectId null); local → project leaf required.
    final pid = entry.usesProjectLeaf ? projectId : null;
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AppScaffold(
          title: Text(entry.label),
          body: CabinetModuleHost(
            cabinetId: cabinetId,
            projectId: pid,
            sessionId: pid != null ? sessionId : null,
            entry: entry,
            embedded: true,
          ),
        ),
      ),
    );
  }

  Widget _listBody(BuildContext context, AppLocalizations l10n) {
    final pid = projectId;
    final visibleEntries = [
      for (final e in entries)
        if (!e.requiresActiveChat ||
            (sessionId != null && sessionId!.trim().isNotEmpty))
          e,
    ];
    final hasCabinetOwned = visibleEntries.any((e) => !e.usesProjectLeaf);
    if ((pid == null || pid.isEmpty) && !hasCabinetOwned) {
      return EmptyPlaceholder(
        title: l10n.projectCreateProjectHint,
        icon: Icons.folder_outlined,
      );
    }
    if (visibleEntries.isEmpty) {
      return EmptyPlaceholder(
        title: l10n.companyNoModules,
        icon: emptyIcon,
      );
    }

    return ListView(
      padding: const EdgeInsets.all(AppSpacing.md),
      children: [
        for (final entry in visibleEntries)
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
    this.sessionId,
    this.embedded = false,
  });

  final String cabinetId;
  final String? projectId;
  final String? sessionId;
  final List<CabinetNavEntry> entries;
  final bool embedded;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return CabinetModuleHubPage(
      cabinetId: cabinetId,
      projectId: projectId,
      sessionId: sessionId,
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
    this.sessionId,
    this.embedded = false,
  });

  final String cabinetId;
  final String? projectId;
  final String? sessionId;
  final List<CabinetNavEntry> entries;
  final bool embedded;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return CabinetModuleHubPage(
      cabinetId: cabinetId,
      projectId: projectId,
      sessionId: sessionId,
      title: l10n.navData,
      entries: entries,
      embedded: embedded,
      emptyIcon: Icons.table_chart_outlined,
    );
  }
}
