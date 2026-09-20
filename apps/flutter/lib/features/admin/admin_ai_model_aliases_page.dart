import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class AdminAiModelAliasesPage extends StatefulWidget {
  const AdminAiModelAliasesPage({
    super.key,
    required this.modelId,
    required String modelName,
  });

  final String modelId;

  static Future<void> push(
    BuildContext context, {
    required String modelId,
    required String modelName,
  }) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AdminAiModelAliasesPage(modelId: modelId, modelName: modelName),
      ),
    );
  }

  @override
  State<AdminAiModelAliasesPage> createState() => _AdminAiModelAliasesPageState();
}

class _AdminAiModelAliasesPageState extends State<AdminAiModelAliasesPage> {
  bool _loading = true;
  bool _saving = false;
  List<String> _aliases = const [];
  late String _displayName;

  @override
  void initState() {
    super.initState();
    _displayName = '';
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final model = await adminContext.api.getAiModel(widget.modelId);
      final ids = model['model_ids'];
      if (!mounted) return;
      setState(() {
        _aliases = ids is List ? ids.whereType<String>().toList() : const [];
        _displayName = model['name'] as String? ?? '';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _persist(List<String> next) async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      await adminContext.api.patchAiModel(modelId: widget.modelId, modelIds: next);
      if (!mounted) return;
      setState(() {
        _aliases = next;
        _saving = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      AppErrors.showSnack(context, e);
      await _load();
    }
  }

  Future<void> _addAlias(String value) async {
    final trimmed = value.trim();
    if (trimmed.isEmpty) return;
    final lower = trimmed.toLowerCase();
    if (_aliases.any((a) => a.toLowerCase() == lower)) return;
    await _persist([..._aliases, trimmed]);
  }

  Future<void> _deleteAlias(String alias) async {
    await _persist(_aliases.where((a) => a != alias).toList());
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _aliases
        .map(
          (alias) => AppEntityRow(
            id: alias,
            title: alias,
          ),
        )
        .toList();

    return AppScaffold(
      title: Text(l10n.aiModelModelIdsLabel),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: EdgeInsets.all(AppSpacing.md),
              children: [
                Text(
                  _displayName,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                SizedBox(height: AppSpacing.md),
                AppInlineAddField(
                  title: l10n.aiKeyModelsAddHint,
                  hintText: l10n.aiKeyModelsAddHint,
                  validator: (v) => v.trim().isNotEmpty,
                  invalidMessage: l10n.errorValidation,
                  onSave: _addAlias,
                ),
                SizedBox(height: AppSpacing.sm),
                AppEntityCollection(
                  rows: rows,
                  loading: _saving,
                  primaryColumnLabel: l10n.aiModelModelIdsLabel,
                  columns: const [],
                  onOpen: (_) {},
                  onDelete: (row) => _deleteAlias(row.id),
                  empty: EmptyPlaceholder(title: l10n.aiKeyModelsEmpty),
                ),
              ],
            ),
    );
  }
}
