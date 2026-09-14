import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/company/company_module_cabinets_page.dart';
import 'package:prodavan/features/company/company_module_json_page.dart';
import 'package:prodavan/features/company/company_module_projects_page.dart';
import 'package:prodavan/features/meta/company_module_meta_repository.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_shell_nav_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company module detail — RO for platform-assigned; nav to cabinets/projects tables.
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
      final manifest = await CompanyModuleMetaRepository.load(
        companyContext.api,
        companyId: widget.companyId,
        moduleId: widget.moduleId,
      );
      if (!mounted) return;
      setState(() {
        _name = mod['name'] as String? ?? widget.moduleName;
        _writable = mod['writable'] == true;
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

  Future<void> _openCabinets() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CompanyModuleCabinetsPage(
          companyId: widget.companyId,
          moduleId: widget.moduleId,
          moduleName: _name,
        ),
      ),
    );
  }

  Future<void> _openProjects() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CompanyModuleProjectsPage(
          companyId: widget.companyId,
          moduleId: widget.moduleId,
          moduleName: _name,
        ),
      ),
    );
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

  Future<void> _openSeedData() async {
    final l10n = AppLocalizations.of(context);
    final manifest = _manifest;
    if (manifest == null || !manifest.hasContent) {
      AppSnackBar.warning(context, l10n.adminModulePreviewEmpty);
      return;
    }
    final entry = shellNavEntryForModuleSeed(
      moduleId: widget.moduleId,
      moduleName: _name,
      manifest: manifest,
    );
    if (entry == null) {
      AppSnackBar.warning(context, l10n.adminModulePreviewEmpty);
      return;
    }
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ModuleShellNavPage(
          entry: entry,
          companyId: widget.companyId,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);

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
                AppNavPreference(
                  title: l10n.commonCabinets,
                  icon: Icons.folder_outlined,
                  onTap: _openCabinets,
                ),
                AppNavPreference(
                  title: l10n.commonProjects,
                  icon: Icons.folder_open_outlined,
                  onTap: _openProjects,
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
                    icon: Icons.storage_outlined,
                    onTap: _openSeedData,
                  ),
              ],
            ),
    );
  }
}
