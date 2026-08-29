import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/features/employee/cabinet_module_host.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/features/employee/cabinet_projects_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Narrow-only hub: Projects + bound module tabs.
class CabinetManagementPage extends StatefulWidget {
  const CabinetManagementPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetManagementPage> createState() => _CabinetManagementPageState();
}

class _CabinetManagementPageState extends State<CabinetManagementPage> {
  bool _loading = true;
  List<CabinetNavEntry> _entries = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final entries = await loadCabinetNavEntries(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _entries = entries;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _open(BuildContext context, Widget page) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(builder: (_) => page),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return AppScaffold(
        title: Text(l10n.navManagement),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    return AppScaffold(
      title: Text(l10n.navManagement),
      body: ListView.separated(
        padding: const EdgeInsets.all(AppSpacing.md),
        itemCount: 1 + _entries.length,
        separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
        itemBuilder: (context, i) {
          if (i == 0) {
            return AppListItem(
              leading: const Icon(Icons.folder_outlined),
              title: Text(l10n.navProjects),
              trailing: const AppTrailingChevron(),
              onTap: () => _open(
                context,
                CabinetProjectsPage(cabinetId: widget.cabinetId),
              ),
            );
          }
          final entry = _entries[i - 1];
          return AppListItem(
            leading: Icon(entry.icon),
            title: Text(entry.label),
            trailing: const AppTrailingChevron(),
            onTap: () => _open(
              context,
              CabinetModuleHost(cabinetId: widget.cabinetId, entry: entry),
            ),
          );
        },
      ),
    );
  }
}
