import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_json_editor_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/meta/module_meta_autosave.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_meta_repository.dart';
import 'package:prodavan/features/meta/module_meta_validator.dart';
import 'package:prodavan/features/meta/preview/module_meta_preview_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin module detail — name, cabinets, JSON manifest, preview.
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
  final _jsonController = TextEditingController();
  final _jsonFieldKey = GlobalKey<AppJsonEditorFieldState>();

  late final ModuleMetaAutosave _autosave;

  bool _loading = true;
  Object? _error;
  String _name = '';
  Set<String> _cabinetIds = {};
  List<Map<String, dynamic>> _cabinets = const [];

  @override
  void initState() {
    super.initState();
    _autosave = ModuleMetaAutosave(
      moduleId: widget.moduleId,
      api: adminContext.api,
    );
    _jsonController.addListener(_onJsonChanged);
    _load();
  }

  @override
  void dispose() {
    _jsonController.removeListener(_onJsonChanged);
    _autosave.dispose();
    _jsonController.dispose();
    super.dispose();
  }

  void _onJsonChanged() {
    final field = _jsonFieldKey.currentState;
    final canSave = field?.isFullyValid ?? false;
    _autosave.onTextChanged(_jsonController.text, canSave: canSave);
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final mod = await adminContext.api.getModule(widget.moduleId);
      final cabinets = await adminContext.api.listCabinets();
      final manifest = await ModuleMetaRepository.load(adminContext.api, widget.moduleId);
      if (!mounted) return;
      final ids = mod['cabinet_ids'];
      final text = manifest.tables.isEmpty &&
              manifest.columns.isEmpty &&
              manifest.views.isEmpty &&
              manifest.tabs.isEmpty
          ? ModuleMetaManifest.empty().toPrettyJson()
          : manifest.toPrettyJson();
      setState(() {
        _name = mod['name'] as String? ?? widget.moduleName;
        _cabinetIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : <String>{};
        _cabinets = cabinets;
        _loading = false;
      });
      _jsonController.text = text;
      _autosave.markSaved(text);
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

  Future<void> _saveCabinets(Set<String> cabinetIds) async {
    if (cabinetIds == _cabinetIds) return;
    try {
      final updated = await adminContext.api.updateModule(
        moduleId: widget.moduleId,
        cabinetIds: cabinetIds.toList(),
      );
      if (!mounted) return;
      final ids = updated['cabinet_ids'];
      setState(() {
        _cabinetIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : cabinetIds;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  String? _validateManifest(Object? parsed) {
    return ModuleMetaValidator.validate(parsed);
  }

  void _openPreview() {
    final field = _jsonFieldKey.currentState;
    if (field == null || !field.isValidJson) return;
    try {
      final manifest = ModuleMetaManifest.fromJson(jsonDecode(_jsonController.text));
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ModuleMetaPreviewPage(
            manifest: manifest,
            moduleName: _name.isEmpty ? widget.moduleName : _name,
          ),
        ),
      );
    } catch (e) {
      AppErrors.showSnack(context, e);
    }
  }

  String _cabinetLabel(String id) {
    for (final c in _cabinets) {
      if (c['id'] == id) {
        return c['name'] as String? ?? id;
      }
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
    final tokens = Theme.of(context).extension<AppColorTokens>()!;
    final cabinetChoices = _cabinets
        .map((c) => c['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    for (final id in _cabinetIds) {
      if (!cabinetChoices.contains(id)) cabinetChoices.insert(0, id);
    }

    final jsonField = _jsonFieldKey.currentState;
    final canPreview = jsonField?.isValidJson ?? false;
    final domainError = jsonField?.errorText;

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
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md).add(
                    const EdgeInsets.only(top: AppSpacing.md, bottom: AppSpacing.sm),
                  ),
                  child: AppSectionHeader(title: l10n.adminModuleJson),
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                  child: AppJsonEditorField(
                    key: _jsonFieldKey,
                    controller: _jsonController,
                    validator: _validateManifest,
                    onChanged: (_) => setState(() {}),
                  ),
                ),
                if (domainError != null &&
                    _jsonController.text.trim().isNotEmpty &&
                    jsonField?.isValidJson == true) ...[
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md).add(
                      const EdgeInsets.only(top: AppSpacing.sm),
                    ),
                    child: AppStatusBanner(
                      severity: AppStatusSeverity.warning,
                      message: domainError,
                    ),
                  ),
                ],
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md).add(
                    const EdgeInsets.only(top: AppSpacing.md, bottom: AppSpacing.lg),
                  ),
                  child: SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      onPressed: canPreview ? _openPreview : null,
                      style: FilledButton.styleFrom(
                        backgroundColor: tokens.warning,
                        foregroundColor: tokens.onWarning,
                        disabledBackgroundColor: tokens.warning.withValues(alpha: 0.35),
                      ),
                      child: Text(l10n.adminModulePreview),
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}
