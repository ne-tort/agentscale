import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_json_editor_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/meta/company_module_meta_repository.dart';
import 'package:prodavan/features/meta/module_meta_autosave.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_meta_validator.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company module JSON — read-only for platform-assigned; autosave for local modules.
/// Seed / «Предзаполнение» is on the module detail screen, not here.
class CompanyModuleJsonPage extends StatefulWidget {
  const CompanyModuleJsonPage({
    super.key,
    required this.companyId,
    required this.moduleId,
    required this.moduleName,
    required this.writable,
  });

  final String companyId;
  final String moduleId;
  final String moduleName;
  final bool writable;

  @override
  State<CompanyModuleJsonPage> createState() => _CompanyModuleJsonPageState();
}

class _CompanyModuleJsonPageState extends State<CompanyModuleJsonPage> {
  final _jsonController = TextEditingController();
  final _jsonFieldKey = GlobalKey<AppJsonEditorFieldState>();
  ModuleMetaAutosave? _autosave;

  bool _loading = true;
  Object? _error;

  @override
  void initState() {
    super.initState();
    if (widget.writable) {
      _autosave = ModuleMetaAutosave(
        onSave: (manifest) => CompanyModuleMetaRepository.save(
          companyContext.api,
          companyId: widget.companyId,
          moduleId: widget.moduleId,
          manifest: manifest,
        ),
      );
      _jsonController.addListener(_onJsonChanged);
    }
    _load();
  }

  @override
  void dispose() {
    _jsonController.removeListener(_onJsonChanged);
    _autosave?.dispose();
    _jsonController.dispose();
    super.dispose();
  }

  void _onJsonChanged() {
    final field = _jsonFieldKey.currentState;
    final canSave = field?.isFullyValid ?? false;
    _autosave?.onTextChanged(_jsonController.text, canSave: canSave);
    setState(() {});
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final manifest = await CompanyModuleMetaRepository.load(
        companyContext.api,
        companyId: widget.companyId,
        moduleId: widget.moduleId,
      );
      if (!mounted) return;
      final text = manifest.hasContent
          ? manifest.toPrettyJson()
          : ModuleMetaManifest.empty().toPrettyJson();
      setState(() => _loading = false);
      _jsonController.text = text;
      _autosave?.markSaved(text);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  String? _validateManifest(Object? parsed) => ModuleMetaValidator.validate(parsed);

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final jsonField = _jsonFieldKey.currentState;
    final domainError = jsonField?.errorText;

    return AppScaffold(
      title: Text(l10n.adminModuleJson),
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
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                  child: AppJsonEditorField(
                    key: _jsonFieldKey,
                    controller: _jsonController,
                    readOnly: !widget.writable,
                    validator: _validateManifest,
                    onChanged: (_) => setState(() {}),
                  ),
                ),
                if (domainError != null &&
                    _jsonController.text.trim().isNotEmpty &&
                    jsonField?.isValidJson == true)
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
            ),
    );
  }
}
