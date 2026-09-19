import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Edit AI model catalog entry — name, SDK bindings, metadata.
class AiModelDetailPage extends StatefulWidget {
  const AiModelDetailPage({
    super.key,
    required this.companyId,
    required this.modelId,
    required this.modelName,
  });

  final String companyId;
  final String modelId;
  final String modelName;

  @override
  State<AiModelDetailPage> createState() => _AiModelDetailPageState();
}

class _AiModelDetailPageState extends State<AiModelDetailPage> {
  bool _loading = true;
  String _name = '';
  String _modelIds = '';
  String _provider = '';
  String _reasoningLevel = '';
  String _description = '';
  String _inputPrice = '';
  String _outputPrice = '';
  String _maxTokens = '';
  String _publisher = '';
  String _releasedAt = '';

  @override
  void initState() {
    super.initState();
    _name = widget.modelName;
    _load();
  }

  String _strField(Object? value) => value == null ? '' : '$value';

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final models = await companyContext.api.listAiModels(widget.companyId);
      final row = models.firstWhere(
        (m) => m['id'] == widget.modelId,
        orElse: () => const {},
      );
      if (!mounted) return;
      setState(() {
        _name = row['name'] as String? ?? widget.modelName;
        final ids = row['model_ids'];
        _modelIds = ids is List ? ids.whereType<String>().join(', ') : '';
        _provider = row['provider'] as String? ?? '';
        _reasoningLevel = row['reasoning_level'] as String? ?? '';
        _description = row['description'] as String? ?? '';
        _inputPrice = _strField(row['input_price_usd_per_mtok']);
        _outputPrice = _strField(row['output_price_usd_per_mtok']);
        _maxTokens = _strField(row['max_context_tokens']);
        _publisher = row['publisher'] as String? ?? '';
        _releasedAt = row['released_at'] as String? ?? '';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveName(String v) async {
    final name = v.trim();
    if (name.isEmpty) return;
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      name: name,
    );
    if (mounted) setState(() => _name = name);
  }

  Future<void> _saveModelIds(String v) async {
    final ids = v
        .split(',')
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      modelIds: ids,
    );
    if (mounted) setState(() => _modelIds = v);
  }

  Future<void> _saveProvider(String v) async {
    final trimmed = v.trim();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      provider: trimmed.isEmpty ? '' : trimmed,
    );
    if (mounted) setState(() => _provider = trimmed);
  }

  Future<void> _saveReasoningLevel(String v) async {
    final trimmed = v.trim();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      reasoningLevel: trimmed.isEmpty ? '' : trimmed,
    );
    if (mounted) setState(() => _reasoningLevel = trimmed);
  }

  Future<void> _saveDescription(String v) async {
    final trimmed = v.trim();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      description: trimmed.isEmpty ? '' : trimmed,
    );
    if (mounted) setState(() => _description = trimmed);
  }

  Future<void> _saveInputPrice(String v) async {
    final trimmed = v.trim();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      inputPriceUsdPerMtok: trimmed.isEmpty ? null : double.tryParse(trimmed),
    );
    if (mounted) setState(() => _inputPrice = trimmed);
  }

  Future<void> _saveOutputPrice(String v) async {
    final trimmed = v.trim();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      outputPriceUsdPerMtok: trimmed.isEmpty ? null : double.tryParse(trimmed),
    );
    if (mounted) setState(() => _outputPrice = trimmed);
  }

  Future<void> _saveMaxTokens(String v) async {
    final trimmed = v.trim();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      maxContextTokens: trimmed.isEmpty ? null : int.tryParse(trimmed),
    );
    if (mounted) setState(() => _maxTokens = trimmed);
  }

  Future<void> _savePublisher(String v) async {
    final trimmed = v.trim();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      publisher: trimmed.isEmpty ? '' : trimmed,
    );
    if (mounted) setState(() => _publisher = trimmed);
  }

  Future<void> _saveReleasedAt(String v) async {
    final trimmed = v.trim();
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      releasedAt: trimmed.isEmpty ? null : trimmed,
    );
    if (mounted) setState(() => _releasedAt = trimmed);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return AppScaffold(
        title: Text(_name),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    return AppScaffold(
      title: Text(_name),
      body: ListView(
        padding: EdgeInsets.all(AppSpacing.md),
        children: [
          AppValuePreference<String>(
            title: l10n.aiModelNameLabel,
            icon: Icons.label_outline,
            value: _name,
            onSave: _saveName,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelKeyAliasesLabel,
            icon: Icons.alternate_email_rounded,
            value: _modelIds,
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveModelIds,
          ),
          AppValuePreference<String>(
            title: l10n.commonProvider,
            icon: Icons.cloud_outlined,
            value: _provider,
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveProvider,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelReasoningLevelLabel,
            icon: Icons.psychology_outlined,
            value: _reasoningLevel,
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveReasoningLevel,
          ),
          AppValuePreference<String>(
            title: l10n.commonDescription,
            icon: Icons.notes_rounded,
            value: _description,
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveDescription,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelInputPriceLabel,
            icon: Icons.payments_outlined,
            value: _inputPrice,
            keyboardType: TextInputType.numberWithOptions(decimal: true),
            onSave: _saveInputPrice,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelOutputPriceLabel,
            icon: Icons.payments_outlined,
            value: _outputPrice,
            keyboardType: TextInputType.numberWithOptions(decimal: true),
            onSave: _saveOutputPrice,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelMaxTokensLabel,
            icon: Icons.memory_outlined,
            value: _maxTokens,
            keyboardType: TextInputType.number,
            onSave: _saveMaxTokens,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelPublisherLabel,
            icon: Icons.business_outlined,
            value: _publisher,
            onSave: _savePublisher,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelReleasedAtLabel,
            icon: Icons.calendar_today_outlined,
            value: _releasedAt,
            hintText: 'YYYY-MM-DD',
            onSave: _saveReleasedAt,
          ),
        ],
      ),
    );
  }
}
