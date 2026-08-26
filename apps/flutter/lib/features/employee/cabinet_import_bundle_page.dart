import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/employee/dynamic_cabinet_shell.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Import cabinet.bundle zip into a new schema (L05/L06 C-BUNDLE).
class CabinetImportBundlePage extends StatefulWidget {
  const CabinetImportBundlePage({super.key});

  @override
  State<CabinetImportBundlePage> createState() => _CabinetImportBundlePageState();
}

class _CabinetImportBundlePageState extends State<CabinetImportBundlePage> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  List<int>? _zipBytes;
  String? _pickedFilename;
  bool _importing = false;
  bool _loadingCatalog = true;
  Object? _error;
  List<Map<String, dynamic>> _starterBundles = const [];

  @override
  void initState() {
    super.initState();
    _loadStarterCatalog();
  }

  Future<void> _loadStarterCatalog() async {
    setState(() {
      _loadingCatalog = true;
      _error = null;
    });
    try {
      final items = await workContext.api.listStarterBundles();
      if (!mounted) return;
      setState(() {
        _starterBundles = items;
        _loadingCatalog = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingCatalog = false;
      });
    }
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _pickBundle() async {
    final l10n = AppLocalizations.of(context);
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['zip'],
      withData: true,
    );
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    final bytes = file.bytes;
    if (bytes == null) {
      setState(() => _error = l10n.cabinetCouldNotReadZipBytes);
      return;
    }
    setState(() {
      _zipBytes = bytes;
      _pickedFilename = file.name;
      _error = null;
      if (_nameCtrl.text.trim().isEmpty) {
        final base = file.name.replaceAll(RegExp(r'\.zip$', caseSensitive: false), '');
        _nameCtrl.text = base.isEmpty ? l10n.cabinetImportedCabinetDefault : base;
      }
    });
  }

  Future<void> _importFromStarter(String bundleId, String defaultName) async {
    final l10n = AppLocalizations.of(context);
    final companyId = workContext.companyId;
    if (companyId == null) {
      setState(() => _error = l10n.cabinetNoCompanyIdFromMe);
      return;
    }
    setState(() {
      _importing = true;
      _error = null;
    });
    try {
      final payload = await workContext.api.downloadStarterBundle(bundleId);
      final b64 = payload['zip_base64'] as String?;
      if (b64 == null || b64.isEmpty) {
        throw StateError('Starter bundle payload missing zip_base64');
      }
      final bytes = base64Decode(b64);
      final name = payload['name'] as String? ?? defaultName;
      final result = await workContext.api.importCabinetBundle(
        companyId: companyId,
        zipBytes: bytes,
        name: name,
      );
      if (!mounted) return;
      final cabinet = result['cabinet'] as Map<String, dynamic>? ?? result;
      final cabinetId = cabinet['id'] as String;
      final cabinetName = cabinet['name'] as String? ?? name;
      workContext.enterCabinet(cabinetId);
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => DynamicCabinetShell(cabinetId: cabinetId, cabinetName: cabinetName),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _importing = false;
      });
    }
  }

  Future<void> _import() async {
    if (!_formKey.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context);
    final companyId = workContext.companyId;
    if (companyId == null) {
      setState(() => _error = l10n.cabinetNoCompanyIdFromMe);
      return;
    }
    final bytes = _zipBytes;
    if (bytes == null || bytes.isEmpty) {
      setState(() => _error = l10n.cabinetSelectCabinetBundleZip);
      return;
    }

    setState(() {
      _importing = true;
      _error = null;
    });
    try {
      final result = await workContext.api.importCabinetBundle(
        companyId: companyId,
        zipBytes: bytes,
        name: _nameCtrl.text.trim(),
      );
      if (!mounted) return;
      final cabinet = result['cabinet'] as Map<String, dynamic>? ?? result;
      final cabinetId = cabinet['id'] as String;
      final cabinetName = cabinet['name'] as String? ?? _nameCtrl.text.trim();
      workContext.enterCabinet(cabinetId);
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => DynamicCabinetShell(cabinetId: cabinetId, cabinetName: cabinetName),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _importing = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.cabinetImportCabinetBundle),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
          Text(l10n.cabinetImportBundleIntro),
          const SizedBox(height: AppSpacing.md),
          if (_loadingCatalog)
            Center(child: Padding(padding: EdgeInsets.all(AppSpacing.md), child: CircularProgressIndicator()))
          else if (_starterBundles.isNotEmpty) ...[
            AppSectionHeader(title: l10n.cabinetOfficialStarterBundles),
            ..._starterBundles.map((item) {
              final id = item['id'] as String? ?? '';
              final available = item['bundle_available'] == true;
              return ListTile(
                leading: Icon(available ? Icons.inventory_2_outlined : Icons.hourglass_empty),
                title: Text(item['name'] as String? ?? id),
                subtitle: Text(item['description'] as String? ?? ''),
                trailing: available
                    ? AppButton(
                        label: _importing ? '…' : l10n.commonImport,
                        onPressed: _importing ? null : () => _importFromStarter(id, item['name'] as String? ?? id),
                      )
                    : Text(l10n.cabinetNotShipped),
              );
            }),
            const SizedBox(height: AppSpacing.lg),
            AppSectionHeader(title: l10n.cabinetFromFile),
          ],
          Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
              OutlinedButton.icon(
                onPressed: _importing ? null : _pickBundle,
                icon: Icon(Icons.folder_open),
                label: Text(_pickedFilename ?? l10n.cabinetChooseZipFile),
              ),
              const SizedBox(height: AppSpacing.md),
              AppSectionHeader(title: l10n.cabinetNewCabinet),
              TextFormField(
                controller: _nameCtrl,
                decoration: InputDecoration(labelText: l10n.cabinetCabinetName),
                enabled: !_importing,
                validator: (v) {
                  if (v == null || v.trim().isEmpty) return l10n.commonNameRequired;
                  return null;
                },
              ),
              const SizedBox(height: AppSpacing.md),
              AppButton(
                label: _importing ? l10n.cabinetImporting : l10n.cabinetImportBundleTooltip,
                onPressed: _importing ? null : _import,
              ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
