import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// AI key probe page — verifies the key secret works against the provider API.
///
/// Shows the last probe result (status, latency, models, error) and a button
/// to re-run the probe. If the probe returned a models list, allows selecting
/// a model (purely informational — the actual model selection lives on the
/// key models page; this just shows what the provider exposed).
class AiKeyProbePage extends StatefulWidget {
  const AiKeyProbePage({
    super.key,
    required this.keyId,
    required this.keyName,
    required this.hasSecret,
    required this.apiKind,
    required this.onProbe,
    required this.onLoadLast,
  });

  final String keyId;
  final String keyName;
  final bool hasSecret;
  final String apiKind;
  final Future<Map<String, dynamic>> Function() onProbe;
  final Future<Map<String, dynamic>> Function() onLoadLast;

  static Future<void> push(
    BuildContext context, {
    required String keyId,
    required String keyName,
    required bool hasSecret,
    required String apiKind,
    required Future<Map<String, dynamic>> Function() onProbe,
    required Future<Map<String, dynamic>> Function() onLoadLast,
  }) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AiKeyProbePage(
          keyId: keyId,
          keyName: keyName,
          hasSecret: hasSecret,
          apiKind: apiKind,
          onProbe: onProbe,
          onLoadLast: onLoadLast,
        ),
      ),
    );
  }

  @override
  State<AiKeyProbePage> createState() => _AiKeyProbePageState();
}

class _AiKeyProbePageState extends State<AiKeyProbePage> {
  bool _loading = true;
  bool _probing = false;
  Map<String, dynamic>? _result;
  String? _selectedModel;

  @override
  void initState() {
    super.initState();
    _loadLast();
  }

  Future<void> _loadLast() async {
    setState(() {
      _loading = true;
    });
    try {
      final result = await widget.onLoadLast();
      if (!mounted) return;
      setState(() {
        _result = result;
        _selectedModel = result['default_model'] as String? ??
            (result['models'] is List && (result['models'] as List).isNotEmpty
                ? (result['models'] as List).first as String?
                : null);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _probe() async {
    if (_probing) return;
    setState(() => _probing = true);
    try {
      final result = await widget.onProbe();
      if (!mounted) return;
      setState(() {
        _result = result;
        final models = result['models'];
        final modelList = models is List ? models.cast<String>() : const <String>[];
        final previous = _selectedModel;
        if (previous != null && modelList.contains(previous)) {
          _selectedModel = previous;
        } else {
          _selectedModel = result['default_model'] as String? ??
              (modelList.isNotEmpty ? modelList.first : null);
        }
        _probing = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _probing = false);
      AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.aiKeyProbeTitle),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: [
                if (!widget.hasSecret)
                  AppStatusBanner(
                    severity: AppStatusSeverity.warning,
                    message: l10n.aiKeyProbeNoSecret,
                  )
                else ...[
                  if (_result == null || _result!['status'] == 'none')
                    AppStatusBanner(
                      severity: AppStatusSeverity.info,
                      message: l10n.aiKeyProbeNeverRun,
                    )
                  else
                    _buildResultCard(context, l10n),
                  const SizedBox(height: AppSpacing.md),
                  _buildProbeButton(context, l10n),
                  if (_result != null &&
                      _result!['status'] == 'ok' &&
                      _hasModels())
                    ...[_buildModelSelector(context, l10n)],
                ],
              ],
            ),
    );
  }

  bool _hasModels() {
    final models = _result?['models'];
    return models is List && models.isNotEmpty;
  }

  Widget _buildResultCard(BuildContext context, AppLocalizations l10n) {
    final status = _result?['status'] as String? ?? 'none';
    final latency = _result?['latency_ms'];
    final errorCode = _result?['error_code'] as String?;
    final errorMsg = _result?['error_message'] as String?;
    final checkedAt = _result?['checked_at'] as String?;
    final models = _result?['models'];
    final modelCount = models is List ? models.length : 0;

    final severity = switch (status) {
      'ok' => AppStatusSeverity.success,
      'error' => AppStatusSeverity.error,
      'unavailable' => AppStatusSeverity.warning,
      _ => AppStatusSeverity.info,
    };

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  switch (status) {
                    'ok' => Icons.check_circle_outline_rounded,
                    'error' => Icons.error_outline_rounded,
                    'unavailable' => Icons.warning_amber_rounded,
                    _ => Icons.help_outline_rounded,
                  },
                  color: _statusColor(context, severity),
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Text(
                    _statusLabel(l10n, status),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            if (latency != null)
              _detailRow(context, l10n.aiKeyProbeLatency, '$latency ms'),
            if (modelCount > 0)
              _detailRow(context, l10n.aiKeyProbeModelsCount, '$modelCount'),
            if (errorCode != null && errorCode.isNotEmpty)
              _detailRow(context, l10n.aiKeyProbeErrorCode, errorCode),
            if (errorMsg != null && errorMsg.isNotEmpty)
              _detailRow(context, l10n.aiKeyProbeErrorMessage, errorMsg),
            if (checkedAt != null && checkedAt.isNotEmpty)
              _detailRow(context, l10n.aiKeyProbeCheckedAt, checkedAt),
          ],
        ),
      ),
    );
  }

  Widget _detailRow(BuildContext context, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 140,
            child: Text(
              label,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          ),
          Expanded(child: Text(value)),
        ],
      ),
    );
  }

  Widget _buildProbeButton(BuildContext context, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
      child: FilledButton.icon(
        onPressed: _probing || !widget.hasSecret ? null : _probe,
        icon: _probing
            ? const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.network_check_rounded),
        label: Text(l10n.aiKeyProbeRun),
      ),
    );
  }

  Widget _buildModelSelector(BuildContext context, AppLocalizations l10n) {
    final models = (_result?['models'] as List?)?.cast<String>() ?? const <String>[];
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(l10n.aiKeyProbeModelSelect,
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: AppSpacing.sm),
          DropdownButtonFormField<String>(
            value: _selectedModel,
            items: models
                .map((m) => DropdownMenuItem<String>(
                      value: m,
                      child: Text(m),
                    ))
                .toList(),
            onChanged: (v) => setState(() => _selectedModel = v),
            decoration: InputDecoration(
              border: const OutlineInputBorder(),
              labelText: l10n.aiKeyProbeModelLabel,
            ),
          ),
        ],
      ),
    );
  }

  Color _statusColor(BuildContext context, AppStatusSeverity severity) {
    final tokens = context.appColors;
    return switch (severity) {
      AppStatusSeverity.success => tokens.success,
      AppStatusSeverity.error => tokens.danger,
      AppStatusSeverity.warning => tokens.warning,
      AppStatusSeverity.info => tokens.info,
      AppStatusSeverity.critical => tokens.danger,
    };
  }

  String _statusLabel(AppLocalizations l10n, String status) {
    switch (status) {
      case 'ok':
        return l10n.aiKeyProbeStatusOk;
      case 'error':
        return l10n.aiKeyProbeStatusError;
      case 'unavailable':
        return l10n.aiKeyProbeStatusUnavailable;
      case 'none':
      default:
        return l10n.aiKeyProbeStatusNone;
    }
  }
}
