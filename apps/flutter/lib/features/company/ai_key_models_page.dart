import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/company/ai_model_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// AI key model selection — enable models and pick default for a key.
class AiKeyModelsPage extends StatefulWidget {
  const AiKeyModelsPage({
    super.key,
    required this.companyId,
    required this.keyId,
    required this.keyName,
    required this.apiKind,
  });

  final String companyId;
  final String keyId;
  final String keyName;
  final String apiKind;

  static Future<void> push(
    BuildContext context, {
    required String companyId,
    required String keyId,
    required String keyName,
    required String apiKind,
  }) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AiKeyModelsPage(
          companyId: companyId,
          keyId: keyId,
          keyName: keyName,
          apiKind: apiKind,
        ),
      ),
    );
  }

  @override
  State<AiKeyModelsPage> createState() => _AiKeyModelsPageState();
}

class _AiKeyModelsPageState extends State<AiKeyModelsPage> {
  bool _loading = true;
  bool _saving = false;
  List<Map<String, dynamic>> _models = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final items = await companyContext.api.listAiKeyModels(
        companyId: widget.companyId,
        keyId: widget.keyId,
      );
      if (!mounted) return;
      setState(() {
        _models = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _persist() async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      final selections = _models
          .map((m) => {
                'model_id': m['id'],
                'enabled': m['enabled'] == true,
                'is_default': m['is_default'] == true,
              })
          .toList();
      final updated = await companyContext.api.updateAiKeyModels(
        companyId: widget.companyId,
        keyId: widget.keyId,
        selections: selections,
      );
      if (!mounted) return;
      setState(() {
        _models = updated;
        _saving = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _toggleEnabled(String modelId, bool enabled) async {
    setState(() {
      _models = _models.map((m) {
        if (m['id'] != modelId) return m;
        return {...m, 'enabled': enabled};
      }).toList();
    });
    await _persist();
  }

  Future<void> _setDefault(String modelId) async {
    setState(() {
      _models = _models.map((m) {
        final id = m['id'] as String?;
        return {
          ...m,
          'enabled': id == modelId ? true : m['enabled'],
          'is_default': id == modelId,
        };
      }).toList();
    });
    await _persist();
  }

  Future<void> _createModel(String name) async {
    final trimmed = name.trim();
    if (trimmed.isEmpty) return;
    try {
      await companyContext.api.createAiModel(
        companyId: widget.companyId,
        name: trimmed,
        apiKinds: [widget.apiKind],
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  void _openModel(AppEntityRow row) {
    Navigator.of(context)
        .push<void>(
          MaterialPageRoute<void>(
            builder: (_) => AiModelDetailPage(
              companyId: widget.companyId,
              modelId: row.id,
              modelName: row.title,
            ),
          ),
        )
        .then((_) => _load());
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _models
        .map(
          (m) => AppEntityRow(
            id: m['id'] as String? ?? '',
            title: m['name'] as String? ?? '',
            cellWidgets: {
              'enabled': Checkbox(
                value: m['enabled'] == true,
                onChanged: _saving
                    ? null
                    : (v) => _toggleEnabled(m['id'] as String, v ?? false),
              ),
              'default': IconButton(
                tooltip: l10n.aiKeyModelDefault,
                onPressed: _saving ? null : () => _setDefault(m['id'] as String),
                icon: Icon(
                  m['is_default'] == true ? Icons.star : Icons.star_border,
                  color: m['is_default'] == true ? Theme.of(context).colorScheme.primary : null,
                ),
              ),
            },
          ),
        )
        .toList();

    return AppScaffold(
      title: Text(l10n.aiKeyModelsTitle),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: EdgeInsets.all(AppSpacing.md),
              children: [
                Text(
                  widget.keyName,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                SizedBox(height: AppSpacing.md),
                AppInlineAddField(
                  title: l10n.aiKeyModelsAddHint,
                  hintText: l10n.aiKeyModelsAddHint,
                  validator: (raw) => raw.trim().isNotEmpty,
                  invalidMessage: l10n.errorValidation,
                  onSave: _createModel,
                ),
                SizedBox(height: AppSpacing.sm),
                AppEntityCollection(
                  rows: rows,
                  loading: _saving,
                  columns: [
                    AppEntityColumn(id: 'enabled', label: l10n.aiKeyModelEnabled, width: 56),
                    AppEntityColumn(id: 'default', label: l10n.aiKeyModelDefault, width: 56),
                  ],
                  onOpen: _openModel,
                  empty: EmptyPlaceholder(title: l10n.aiKeyModelsEmpty),
                ),
              ],
            ),
    );
  }
}
