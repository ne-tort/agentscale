import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin cabinet detail — name, module bindings, company grants.
class AdminCabinetDetailPage extends StatefulWidget {
  const AdminCabinetDetailPage({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<AdminCabinetDetailPage> createState() => _AdminCabinetDetailPageState();
}

class _AdminCabinetDetailPageState extends State<AdminCabinetDetailPage> {
  bool _loading = true;
  Object? _error;
  String _name = '';
  Set<String> _companyIds = {};
  Set<String> _moduleIds = {};
  String _companyGrantScope = 'selected';
  List<Map<String, dynamic>> _companies = const [];
  List<Map<String, dynamic>> _modules = const [];

  @override
  void initState() {
    super.initState();
    _name = widget.cabinetName;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final cab = await adminContext.api.getCabinet(widget.cabinetId);
      final companies = await adminContext.api.listCompanies();
      final modules = await adminContext.api.listModules();
      if (!mounted) return;
      final companyIds = cab['company_ids'];
      final moduleIds = cab['module_ids'];
      setState(() {
        _name = cab['name'] as String? ?? widget.cabinetName;
        _companyIds = companyIds is List
            ? companyIds.map((e) => e.toString()).toSet()
            : <String>{};
        _moduleIds = moduleIds is List
            ? moduleIds.map((e) => e.toString()).toSet()
            : <String>{};
        _companyGrantScope =
            cab['company_grant_scope'] as String? ?? 'selected';
        _companies = companies;
        _modules = modules;
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

  Future<void> _saveName(String value) async {
    final trimmed = value.trim();
    if (trimmed.isEmpty || trimmed == _name) return;
    try {
      final updated = await adminContext.api.updateCabinet(
        cabinetId: widget.cabinetId,
        name: trimmed,
      );
      if (!mounted) return;
      setState(() {
        _name = updated['name'] as String? ?? trimmed;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  Future<void> _saveModules(Set<String> moduleIds) async {
    if (moduleIds == _moduleIds) return;
    try {
      final updated = await adminContext.api.updateCabinet(
        cabinetId: widget.cabinetId,
        moduleIds: moduleIds.toList(),
      );
      if (!mounted) return;
      final ids = updated['module_ids'];
      setState(() {
        _moduleIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : moduleIds;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  Future<void> _saveGrantScope(String scope) async {
    if (scope == _companyGrantScope) return;
    try {
      final updated = await adminContext.api.updateCabinet(
        cabinetId: widget.cabinetId,
        companyGrantScope: scope,
      );
      if (!mounted) return;
      setState(() {
        _companyGrantScope =
            updated['company_grant_scope'] as String? ?? scope;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  Future<void> _saveCompanies(Set<String> companyIds) async {
    if (companyIds == _companyIds) return;
    try {
      final updated = await adminContext.api.updateCabinet(
        cabinetId: widget.cabinetId,
        companyIds: companyIds.toList(),
      );
      if (!mounted) return;
      final ids = updated['company_ids'];
      setState(() {
        _companyIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : companyIds;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  String _companyLabel(String id) {
    for (final c in _companies) {
      if (c['id'] == id) {
        return c['name'] as String? ?? id;
      }
    }
    return id;
  }

  String _moduleLabel(String id) {
    for (final m in _modules) {
      if (m['id'] == id) {
        return m['name'] as String? ?? id;
      }
    }
    return id;
  }

  String _bindingsSubtitle(
    Set<String> ids,
    String Function(String) label,
  ) {
    if (ids.isEmpty) return '0';
    if (ids.length == 1) return label(ids.first);
    return '${ids.length}';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final companyChoices = _companies
        .map((c) => c['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    for (final id in _companyIds) {
      if (!companyChoices.contains(id)) companyChoices.insert(0, id);
    }
    final moduleChoices = _modules
        .map((m) => m['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    for (final id in _moduleIds) {
      if (!moduleChoices.contains(id)) moduleChoices.insert(0, id);
    }

    return AppScaffold(
      title: Text(_name.isEmpty ? widget.cabinetName : _name),
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
                AppValuePreference<String>(
                  title: l10n.commonName,
                  icon: Icons.label_outline_rounded,
                  value: _name,
                  validateInput: (v) => v.trim().isNotEmpty,
                  onSave: _saveName,
                ),
                AppMultiChoicePreference<String>(
                  title: l10n.navModules,
                  icon: Icons.extension_outlined,
                  values: _moduleIds,
                  choices: moduleChoices,
                  keyFor: (v) => v,
                  labelFor: _moduleLabel,
                  presentValues: (ids) =>
                      _bindingsSubtitle(ids, _moduleLabel),
                  pickerTitle: l10n.adminSelectModulesForCabinet,
                  onSave: _saveModules,
                ),
                AppSwitchPreference(
                  title: l10n.adminGrantAllCompanies,
                  value: _companyGrantScope == 'all',
                  onChanged: (v) async {
                    await _saveGrantScope(v ? 'all' : 'selected');
                  },
                ),
                if (_companyGrantScope != 'all')
                  AppMultiChoicePreference<String>(
                    title: l10n.commonCompanies,
                    icon: Icons.business_outlined,
                    values: _companyIds,
                    choices: companyChoices,
                    keyFor: (v) => v,
                    labelFor: _companyLabel,
                    presentValues: (ids) =>
                        _bindingsSubtitle(ids, _companyLabel),
                    pickerTitle: l10n.adminSelectCompanyForCabinet,
                    onSave: _saveCompanies,
                  ),
              ],
            ),
    );
  }
}
