import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/company/company_module_json_page.dart';
import 'package:prodavan/features/meta/company_module_meta_repository.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_shell_nav_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company module detail — RO for platform-assigned; cabinet bind for all visible modules.
class CompanyModuleDetailPage extends StatefulWidget {
  const CompanyModuleDetailPage({
    super.key,
    required this.companyId,
    required this.moduleId,
    required this.moduleName,
  });

  final String companyId;
  final String moduleId;
  final String moduleName;

  @override
  State<CompanyModuleDetailPage> createState() => _CompanyModuleDetailPageState();
}

class _CompanyModuleDetailPageState extends State<CompanyModuleDetailPage> {
  bool _loading = true;
  Object? _error;
  String _name = '';
  bool _writable = false;
  Set<String> _cabinetIds = {};
  List<Map<String, dynamic>> _cabinets = const [];
  bool _jsonConfigured = false;
  ModuleMetaManifest? _manifest;

  @override
  void initState() {
    super.initState();
    _name = widget.moduleName;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final mod = await companyContext.api.getModule(
        companyId: widget.companyId,
        moduleId: widget.moduleId,
      );
      final cabinets = await companyContext.api.listOrgCabinets(widget.companyId);
      final manifest = await CompanyModuleMetaRepository.load(
        companyContext.api,
        companyId: widget.companyId,
        moduleId: widget.moduleId,
      );
      if (!mounted) return;
      final ids = mod['cabinet_ids'];
      setState(() {
        _name = mod['name'] as String? ?? widget.moduleName;
        _writable = mod['writable'] == true;
        _cabinetIds = ids is List ? ids.map((e) => e.toString()).toSet() : <String>{};
        _cabinets = cabinets;
        _jsonConfigured = manifest.hasContent;
        _manifest = manifest;
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
      final updated = await companyContext.api.updateModule(
        companyId: widget.companyId,
        moduleId: widget.moduleId,
        name: trimmed,
      );
      if (!mounted) return;
      setState(() => _name = updated['name'] as String? ?? trimmed);
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  Future<void> _saveCabinets(Set<String> cabinetIds) async {
    if (cabinetIds == _cabinetIds) return;
    try {
      final updated = await companyContext.api.updateModule(
        companyId: widget.companyId,
        moduleId: widget.moduleId,
        cabinetIds: cabinetIds.toList(),
      );
      if (!mounted) return;
      final ids = updated['cabinet_ids'];
      setState(() {
        _cabinetIds = ids is List ? ids.map((e) => e.toString()).toSet() : cabinetIds;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  Future<void> _openJsonPage() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CompanyModuleJsonPage(
          companyId: widget.companyId,
          moduleId: widget.moduleId,
          moduleName: _name,
          writable: _writable,
        ),
      ),
    );
    if (!mounted) return;
    await _load();
  }

  Future<void> _openPreview() async {
    final manifest = _manifest;
    if (manifest == null || !manifest.hasContent) return;
    final entry = shellNavEntryForModuleSeed(
      moduleId: widget.moduleId,
      moduleName: _name,
      manifest: manifest,
    );
    if (entry == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ModuleShellNavPage(
          entry: entry,
          companyId: widget.companyId,
        ),
      ),
    );
  }

  String _cabinetLabel(String id) {
    for (final c in _cabinets) {
      if (c['id'] == id) return c['name'] as String? ?? id;
    }
    return id;
  }

  String _cabinetsSubtitle(Set<String> ids, AppLocalizations l10n) {
    if (ids.isEmpty) return l10n.commonNotSet;
    if (ids.length == 1) return _cabinetLabel(ids.first);
    return l10n.adminModuleCabinetsCount(ids.length);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final cabinetChoices = _cabinets
        .map((c) => c['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    for (final id in _cabinetIds) {
      if (!cabinetChoices.contains(id)) cabinetChoices.insert(0, id);
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
                  enabled: _writable,
                  validateInput: (v) => v.trim().isNotEmpty,
                  onSave: _saveName,
                ),
                if (cabinetChoices.isNotEmpty)
                  AppMultiChoicePreference<String>(
                    title: l10n.commonCabinets,
                    icon: Icons.folder_outlined,
                    values: _cabinetIds,
                    choices: cabinetChoices,
                    keyFor: (v) => v,
                    labelFor: _cabinetLabel,
                    presentValues: (ids) => _cabinetsSubtitle(ids, l10n),
                    pickerTitle: l10n.adminSelectCabinetsForModule,
                    onSave: _saveCabinets,
                  ),
                AppNavPreference(
                  title: l10n.adminModuleJson,
                  icon: Icons.data_object_outlined,
                  subtitle: Text(
                    _jsonConfigured ? l10n.adminModuleJsonConfigured : l10n.commonNotSet,
                  ),
                  onTap: _openJsonPage,
                ),
                if (_jsonConfigured && _manifest != null && _manifest!.hasContent)
                  AppNavPreference(
                    title: l10n.adminModulePreview,
                    icon: Icons.visibility_outlined,
                    onTap: _openPreview,
                  ),
              ],
            ),
    );
  }
}
