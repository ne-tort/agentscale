import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_json_editor_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/meta/module_meta_autosave.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_meta_repository.dart';
import 'package:prodavan/features/meta/module_meta_validator.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin module manifest JSON editor — autosave only (seed UI lives on module detail).
class AdminModuleJsonPage extends StatefulWidget {
  const AdminModuleJsonPage({
    super.key,
    required this.moduleId,
    required this.moduleName,
  });

  final String moduleId;
  final String moduleName;

  @override
  State<AdminModuleJsonPage> createState() => _AdminModuleJsonPageState();
}

class _AdminModuleJsonPageState extends State<AdminModuleJsonPage> {
  final _jsonController = TextEditingController();
  final _jsonFieldKey = GlobalKey<AppJsonEditorFieldState>();

  late final ModuleMetaAutosave _autosave;

  bool _loading = true;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _autosave = ModuleMetaAutosave(
      onSave: (manifest) => ModuleMetaRepository.save(adminContext.api, widget.moduleId, manifest),
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
    setState(() {});
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final manifest = await ModuleMetaRepository.load(adminContext.api, widget.moduleId);
      if (!mounted) return;
      final text = manifest.hasContent
          ? manifest.toPrettyJson()
          : ModuleMetaManifest.empty().toPrettyJson();
      setState(() => _loading = false);
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

  String? _validateManifest(Object? parsed) {
    return ModuleMetaValidator.validate(parsed);
  }

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
