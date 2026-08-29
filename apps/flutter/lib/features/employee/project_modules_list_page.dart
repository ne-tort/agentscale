import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/employee/project_module_edit_page.dart';
import 'package:prodavan/features/employee/project_modules_table.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project modules list — enable/disable and open module editor.
class ProjectModulesListPage extends StatefulWidget {
  const ProjectModulesListPage({
    super.key,
    required this.cabinetId,
    required this.projectId,
    required this.projectName,
  });

  final String cabinetId;
  final String projectId;
  final String projectName;

  @override
  State<ProjectModulesListPage> createState() => _ProjectModulesListPageState();
}

class _ProjectModulesListPageState extends State<ProjectModulesListPage> {
  bool _loading = true;
  List<Map<String, dynamic>> _modules = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final modules = await workContext.api.listProjectModules(widget.projectId);
      if (!mounted) return;
      setState(() {
        _modules = modules;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _toggleModule(String moduleId, bool enabled) async {
    final ids = _modules
        .where((m) => m['enabled'] == true)
        .map((m) => m['module_id'] as String)
        .whereType<String>()
        .toSet();
    if (enabled) {
      ids.add(moduleId);
    } else {
      ids.remove(moduleId);
    }
    try {
      await workContext.api.patchProjectModules(
        widget.projectId,
        moduleIds: ids.toList(),
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _openModule(Map<String, dynamic> module) async {
    final moduleId = module['module_id'] as String?;
    if (moduleId == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectModuleEditPage(
          cabinetId: widget.cabinetId,
          projectId: widget.projectId,
          moduleId: moduleId,
          moduleName: module['name'] as String? ?? moduleId,
        ),
      ),
    );
    if (mounted) await _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.projectModulesLabel),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Padding(
              padding: EdgeInsets.all(AppSpacing.md),
              child: ProjectModulesTable(
                modules: _modules,
                onOpen: _openModule,
                onEnabledChanged: _toggleModule,
                showHeader: false,
              ),
            ),
    );
  }
}
