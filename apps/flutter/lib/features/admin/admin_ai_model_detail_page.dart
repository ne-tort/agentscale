import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin AI model detail — edit name, key aliases, provider, metadata.
class AdminAiModelDetailPage extends StatefulWidget {
  const AdminAiModelDetailPage({
    super.key,
    required this.modelId,
    required this.modelName,
  });

  final String modelId;
  final String modelName;

  @override
  State<AdminAiModelDetailPage> createState() => _AdminAiModelDetailPageState();
}

class _AdminAiModelDetailPageState extends State<AdminAiModelDetailPage> {
  bool _loading = true;
  String _displayName = '';
  Map<String, dynamic>? _model;

  static const _sdkChoices = [
    'cursor_sdk',
    'codex_sdk',
    'claude_agent_sdk',
    'openclaw_sdk',
  ];

  @override
  void initState() {
    super.initState();
    _displayName = widget.modelName;
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final model = await adminContext.api.getAiModel(widget.modelId);
      if (!mounted) return;
      setState(() {
        _model = model;
        _displayName = model['name'] as String? ?? widget.modelName;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  String _strField(Object? value) => value == null ? '' : '$value';

  List<String> _aliasesList() {
    final aliases = _model?['key_aliases'];
    if (aliases is List) return aliases.whereType<String>().toList();
    return const [];
  }

  Set<String> _apiKindsSet() {
    final kinds = _model?['api_kinds'];
    if (kinds is List) return kinds.map((e) => '$e').toSet();
    return const {};
  }

  Future<void> _saveName(String v) async {
    final name = v.trim();
    if (name.isEmpty) return;
    try {
      await adminContext.api.patchAiModel(modelId: widget.modelId, name: name);
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveAliases(String v) async {
    final aliases = v
        .split(',')
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        keyAliases: aliases,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveProvider(String v) async {
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        provider: v.trim().isEmpty ? null : v.trim(),
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveReasoning(String v) async {
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        reasoningLevel: v.trim().isEmpty ? null : v.trim(),
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveDescription(String v) async {
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        description: v.trim().isEmpty ? null : v.trim(),
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _savePublisher(String v) async {
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        publisher: v.trim().isEmpty ? null : v.trim(),
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveMaxTokens(String v) async {
    final n = int.tryParse(v.trim());
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        maxContextTokens: n,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveInputPrice(String v) async {
    final n = double.tryParse(v.trim());
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        inputPriceUsdPerMtok: n,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveOutputPrice(String v) async {
    final n = double.tryParse(v.trim());
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        outputPriceUsdPerMtok: n,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _saveApiKinds(Set<String> kinds) async {
    try {
      await adminContext.api.patchAiModel(
        modelId: widget.modelId,
        apiKinds: kinds.toList(),
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return AppScaffold(
        title: Text(_displayName),
        body: const Center(child: CircularProgressIndicator()),
      );
    }
    final model = _model ?? const <String, dynamic>{};
    final apiKinds = _apiKindsSet();
    return AppScaffold(
      title: Text(_displayName),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          AppValuePreference<String>(
            title: l10n.aiModelNameLabel,
            icon: Icons.label_outline_rounded,
            value: _displayName,
            onSave: _saveName,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelKeyAliasesLabel,
            icon: Icons.alternate_email_rounded,
            value: _aliasesList().join(', '),
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveAliases,
          ),
          AppValuePreference<String>(
            title: l10n.commonProvider,
            icon: Icons.cloud_outlined,
            value: model['provider'] as String? ?? '',
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveProvider,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelReasoningLevelLabel,
            icon: Icons.psychology_outlined,
            value: model['reasoning_level'] as String? ?? '',
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveReasoning,
          ),
          AppValuePreference<String>(
            title: l10n.commonDescription,
            icon: Icons.notes_rounded,
            value: model['description'] as String? ?? '',
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveDescription,
          ),
          AppMultiChoicePreference<String>(
            title: l10n.aiModelSdkLabel,
            icon: Icons.extension_outlined,
            values: apiKinds,
            choices: _sdkChoices,
            keyFor: (v) => v,
            labelFor: (v) => v,
            presentValues: (ids) => ids.isEmpty ? l10n.commonNotSet : ids.join(', '),
            onSave: _saveApiKinds,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelInputPriceLabel,
            icon: Icons.input_rounded,
            value: _strField(model['input_price_usd_per_mtok']),
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveInputPrice,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelOutputPriceLabel,
            icon: Icons.output_rounded,
            value: _strField(model['output_price_usd_per_mtok']),
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveOutputPrice,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelMaxTokensLabel,
            icon: Icons.memory_outlined,
            value: _strField(model['max_context_tokens']),
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _saveMaxTokens,
          ),
          AppValuePreference<String>(
            title: l10n.aiModelPublisherLabel,
            icon: Icons.business_outlined,
            value: model['publisher'] as String? ?? '',
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: _savePublisher,
          ),
        ],
      ),
    );
  }
}
