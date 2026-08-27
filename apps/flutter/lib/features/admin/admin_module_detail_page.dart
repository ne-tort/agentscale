import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/admin/admin_module_json_page.dart';
import 'package:prodavan/features/meta/module_meta_repository.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin module detail — name and company grants (Company binds cabinets).
class AdminModuleDetailPage extends StatefulWidget {
  const AdminModuleDetailPage({
    super.key,
    required this.moduleId,
    required this.moduleName,
  });

  final String moduleId;
  final String moduleName;

  @override
  State<AdminModuleDetailPage> createState() => _AdminModuleDetailPageState();
}

class _AdminModuleDetailPageState extends State<AdminModuleDetailPage> {
  bool _loading = true;
  Object? _error;
  String _name = '';
  Set<String> _companyIds = {};
  List<Map<String, dynamic>> _companies = const [];
  bool _jsonConfigured = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final mod = await adminContext.api.getModule(widget.moduleId);
      final companies = await adminContext.api.listCompanies();
      final manifest = await ModuleMetaRepository.load(adminContext.api, widget.moduleId);
      if (!mounted) return;
      final ids = mod['company_ids'];
      setState(() {
        _name = mod['name'] as String? ?? widget.moduleName;
        _companyIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : <String>{};
        _companies = companies;
        _jsonConfigured = manifest.hasContent;
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
      final updated = await adminContext.api.updateModule(
        moduleId: widget.moduleId,
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

  Future<void> _saveCompanies(Set<String> companyIds) async {
    if (companyIds == _companyIds) return;
    try {
      final updated = await adminContext.api.updateModule(
        moduleId: widget.moduleId,
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

  Future<void> _openJsonPage() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AdminModuleJsonPage(
          moduleId: widget.moduleId,
          moduleName: _name.isEmpty ? widget.moduleName : _name,
        ),
      ),
    );
    if (!mounted) return;
    await _load();
  }

  String _companyLabel(String id) {
    for (final c in _companies) {
      if (c['id'] == id) return c['name'] as String? ?? id;
    }
    return id;
  }

  String _companiesSubtitle(Set<String> ids, AppLocalizations l10n) {
    if (ids.isEmpty) return l10n.commonNotSet;
    if (ids.length == 1) return _companyLabel(ids.first);
    return l10n.adminBindingsCount(ids.length);
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

    return AppScaffold(
      title: Text(_name.isEmpty ? widget.moduleName : _name),
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
                if (companyChoices.isNotEmpty)
                  AppMultiChoicePreference<String>(
                    title: l10n.navCompanies,
                    icon: Icons.business_outlined,
                    values: _companyIds,
                    choices: companyChoices,
                    keyFor: (v) => v,
                    labelFor: _companyLabel,
                    presentValues: (ids) => _companiesSubtitle(ids, l10n),
                    pickerTitle: l10n.adminSelectCompaniesForModule,
                    onSave: _saveCompanies,
                  ),
                AppNavPreference(
                  title: l10n.adminModuleJson,
                  icon: Icons.data_object_outlined,
                  subtitle: Text(
                    _jsonConfigured
                        ? l10n.adminModuleJsonConfigured
                        : l10n.commonNotSet,
                  ),
                  onTap: _openJsonPage,
                ),
              ],
            ),
    );
  }
}
