import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_radio.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_module_host.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project-scoped module editor — profile pick (if any) + module meta UI.
class ProjectModuleEditPage extends StatefulWidget {
  const ProjectModuleEditPage({
    super.key,
    required this.cabinetId,
    required this.projectId,
    required this.moduleId,
    required this.moduleName,
  });

  final String cabinetId;
  final String projectId;
  final String moduleId;
  final String moduleName;

  @override
  State<ProjectModuleEditPage> createState() => _ProjectModuleEditPageState();
}

class _ProjectModuleEditPageState extends State<ProjectModuleEditPage> {
  bool _loading = true;
  bool _saving = false;
  Map<String, dynamic>? _module;
  CabinetNavEntry? _navEntry;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final mod = await workContext.api.getProjectModule(
        widget.projectId,
        widget.moduleId,
      );
      final entry = await loadFirstModuleNavEntry(widget.cabinetId, widget.moduleId);
      if (!mounted) return;
      setState(() {
        _module = mod;
        _navEntry = entry;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  String? get _selectedProfileId {
    final profiles = _module?['profiles'];
    if (profiles is! List) return _module?['profile_id'] as String?;
    for (final p in profiles) {
      if (p is Map && p['selected'] == true) {
        return p['profile_id'] as String?;
      }
    }
    return _module?['profile_id'] as String?;
  }

  Future<void> _pickProfile(String profileId) async {
    if (_selectedProfileId == profileId) return;
    setState(() => _saving = true);
    try {
      final updated = await workContext.api.patchProjectModuleProfile(
        projectId: widget.projectId,
        moduleId: widget.moduleId,
        profileId: profileId,
      );
      if (!mounted) return;
      setState(() {
        _module = updated;
        _saving = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return AppScaffold(
        title: Text(widget.moduleName),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final mod = _module;
    if (mod == null) {
      return AppScaffold(
        title: Text(widget.moduleName),
        body: EmptyPlaceholder(
          title: l10n.companyNoModules,
          icon: Icons.extension_outlined,
        ),
      );
    }

    final hasProfiles = mod['has_profiles'] == true;
    final profiles = (mod['profiles'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final selectedId = _selectedProfileId;
    final entry = _navEntry;

    return AppScaffold(
      title: Text(widget.moduleName),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (hasProfiles) ...[
            Padding(
              padding: EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.md, AppSpacing.md, AppSpacing.sm),
              child: Text(
                l10n.projectSelectModuleProfile,
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            if (_saving) const LinearProgressIndicator(),
            if (profiles.isNotEmpty)
              SizedBox(
                height: (profiles.length * 48.0).clamp(96, 240),
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: AppSpacing.md),
                  child: AppEntityCollection(
                    mode: AppEntityCollectionMode.table,
                    rows: [
                      for (final p in profiles)
                        AppEntityRow(
                          id: p['profile_id'] as String,
                          title: p['name'] as String? ?? p['profile_id'] as String,
                          cellWidgets: {
                            'select': AppRadio<String>(
                              value: p['profile_id'] as String,
                              groupValue: selectedId,
                              onChanged: _saving
                                  ? null
                                  : (_) => _pickProfile(p['profile_id'] as String),
                            ),
                          },
                        ),
                    ],
                    primaryColumnLabel: l10n.commonName,
                    columns: [
                      AppEntityColumn(
                        id: 'select',
                        label: '',
                        width: 48,
                        align: AppEntityColumnAlign.center,
                      ),
                    ],
                    onOpen: (row) => _pickProfile(row.id),
                  ),
                ),
              ),
            SizedBox(height: AppSpacing.sm),
          ],
          Expanded(
            child: entry == null
                ? Center(child: EmptyPlaceholder(title: l10n.adminMetaInvalid))
                : CabinetModuleHost(
                    cabinetId: widget.cabinetId,
                    entry: entry,
                    embedded: true,
                  ),
          ),
        ],
      ),
    );
  }
}
