import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/company/company_entity_source.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company cabinet detail — module bindings + employee assignments.
class CompanyCabinetDetailPage extends StatefulWidget {
  const CompanyCabinetDetailPage({
    super.key,
    required this.companyId,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String companyId;
  final String cabinetId;
  final String cabinetName;

  @override
  State<CompanyCabinetDetailPage> createState() => _CompanyCabinetDetailPageState();
}

class _CompanyCabinetDetailPageState extends State<CompanyCabinetDetailPage> {
  bool _loading = true;
  Object? _error;
  Set<String> _moduleIds = {};
  Set<String> _employeeIds = {};
  List<Map<String, dynamic>> _modules = const [];
  List<Map<String, dynamic>> _employees = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final assignments = await companyContext.api.listCabinetAssignments(
        companyId: widget.companyId,
        cabinetId: widget.cabinetId,
      );
      final employees = await companyContext.api.listEmployees(widget.companyId);
      final modules = await companyContext.api.listModules(widget.companyId);
      final boundModuleIds = modules
          .where((m) => companyModuleBoundToCabinet(m, widget.cabinetId))
          .map((m) => m['id'] as String)
          .toSet();
      final assignedEmployeeIds = assignments
          .map((a) => a['employee_id'] as String?)
          .whereType<String>()
          .toSet();
      if (!mounted) return;
      setState(() {
        _modules = modules;
        _employees = employees;
        _moduleIds = boundModuleIds;
        _employeeIds = assignedEmployeeIds;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _saveModules(Set<String> moduleIds) async {
    if (moduleIds == _moduleIds) return;
    try {
      for (final module in _modules) {
        final moduleId = module['id'] as String;
        final shouldBind = moduleIds.contains(moduleId);
        final isBound = _moduleIds.contains(moduleId);
        if (shouldBind == isBound) continue;

        final rawIds = module['cabinet_ids'];
        final currentIds = rawIds is List
            ? rawIds.map((e) => e.toString()).toSet()
            : <String>{};
        if (shouldBind) {
          currentIds.add(widget.cabinetId);
        } else {
          currentIds.remove(widget.cabinetId);
        }
        await companyContext.api.updateModule(
          companyId: widget.companyId,
          moduleId: moduleId,
          cabinetIds: currentIds.toList(),
        );
      }
      if (!mounted) return;
      setState(() => _moduleIds = moduleIds);
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  Future<void> _saveEmployees(Set<String> employeeIds) async {
    if (employeeIds == _employeeIds) return;
    try {
      final added = employeeIds.difference(_employeeIds);
      final removed = _employeeIds.difference(employeeIds);
      for (final employeeId in added) {
        await companyContext.api.assignCabinetEmployee(
          companyId: widget.companyId,
          cabinetId: widget.cabinetId,
          employeeId: employeeId,
        );
      }
      for (final employeeId in removed) {
        await companyContext.api.revokeCabinetAssignment(
          companyId: widget.companyId,
          cabinetId: widget.cabinetId,
          employeeId: employeeId,
        );
      }
      if (!mounted) return;
      setState(() => _employeeIds = employeeIds);
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  String _moduleLabel(String id) {
    for (final m in _modules) {
      if (m['id'] == id) return m['name'] as String? ?? id;
    }
    return id;
  }

  String _employeeLabel(String id) {
    for (final e in _employees) {
      if (e['id'] == id) {
        return e['email'] as String? ?? e['display_name'] as String? ?? id;
      }
    }
    return id;
  }

  String _bindingsSubtitle(Set<String> ids, String Function(String) label) {
    if (ids.isEmpty) return '0';
    if (ids.length == 1) return label(ids.first);
    return '${ids.length}';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final moduleChoices = _modules
        .map((m) => m['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    for (final id in _moduleIds) {
      if (!moduleChoices.contains(id)) moduleChoices.insert(0, id);
    }
    final employeeChoices = _employees
        .where((e) => e['status'] != 'disabled')
        .map((e) => e['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    for (final id in _employeeIds) {
      if (!employeeChoices.contains(id)) employeeChoices.insert(0, id);
    }

    return AppScaffold(
      title: Text(widget.cabinetName),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: [
                if (_error != null)
                  AppStatusBanner(
                    severity: AppStatusSeverity.error,
                    message: AppErrors.localize(context, _error!),
                  ),
                if (moduleChoices.isNotEmpty)
                  AppMultiChoicePreference<String>(
                    title: l10n.navModules,
                    icon: Icons.extension_outlined,
                    values: _moduleIds,
                    choices: moduleChoices,
                    keyFor: (v) => v,
                    labelFor: _moduleLabel,
                    presentValues: (ids) => _bindingsSubtitle(ids, _moduleLabel),
                    pickerTitle: l10n.adminSelectModulesForCabinet,
                    onSave: _saveModules,
                  ),
                if (employeeChoices.isNotEmpty)
                  AppMultiChoicePreference<String>(
                    title: l10n.companyAssignedEmployees,
                    icon: Icons.people_outline,
                    values: _employeeIds,
                    choices: employeeChoices,
                    keyFor: (v) => v,
                    labelFor: _employeeLabel,
                    presentValues: (ids) => _bindingsSubtitle(ids, _employeeLabel),
                    pickerTitle: l10n.companyAssignEmployeeToCabinet,
                    onSave: _saveEmployees,
                  ),
              ],
            ),
    );
  }
}
