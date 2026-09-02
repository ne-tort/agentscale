import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Edit AI model catalog entry — name + SDK bindings.
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
  Set<String> _apiKinds = const {};

  static const _sdkChoices = [
    'cursor_sdk',
    'codex_sdk',
    'claude_agent_sdk',
    'openclaw_sdk',
  ];

  @override
  void initState() {
    super.initState();
    _name = widget.modelName;
    _load();
  }

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
        final kinds = row['api_kinds'];
        _apiKinds = kinds is List ? kinds.map((e) => '$e').toSet() : const {};
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

  Future<void> _saveSdks(Set<String> kinds) async {
    await companyContext.api.patchAiModel(
      companyId: widget.companyId,
      modelId: widget.modelId,
      apiKinds: kinds.toList(),
    );
    if (mounted) setState(() => _apiKinds = kinds);
  }

  String _sdkLabel(AppLocalizations l10n, String kind) {
    switch (kind) {
      case 'cursor_sdk':
        return l10n.adminTypeCursorSdk;
      case 'codex_sdk':
        return l10n.adminTypeCodexSdk;
      case 'claude_agent_sdk':
        return l10n.adminTypeClaudeSdk;
      case 'openclaw_sdk':
        return 'OpenClaw SDK';
      default:
        return kind;
    }
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
          AppMultiChoicePreference<String>(
            title: l10n.aiModelSdkLabel,
            icon: Icons.hub_outlined,
            values: _apiKinds,
            choices: _sdkChoices,
            keyFor: (v) => v,
            labelFor: (v) => _sdkLabel(l10n, v),
            onSave: _saveSdks,
          ),
        ],
      ),
    );
  }
}
