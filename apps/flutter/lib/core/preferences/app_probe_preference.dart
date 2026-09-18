import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Inline "Verify key" preference tile.
///
/// Tapping the row runs the probe inline (no separate page). While probing,
/// the trailing shows a spinner; after completion the subtitle shows the
/// last probe result (status · latency · date) and, on error/unavailable,
/// the error details. If the probe returned a models list, a model picker
/// appears below the tile.
class AppProbePreference extends StatefulWidget {
  const AppProbePreference({
    super.key,
    required this.enabled,
    required this.lastProbe,
    required this.onProbe,
    this.accentColor,
  });

  /// Whether the key has a secret (probe is meaningful only then).
  final bool enabled;

  /// Last stored probe result (map with status/latency_ms/models/etc.) or null.
  final Map<String, dynamic>? lastProbe;

  /// Fires the probe; resolves with the fresh result map.
  final Future<Map<String, dynamic>> Function() onProbe;

  final Color? accentColor;

  @override
  State<AppProbePreference> createState() => _AppProbePreferenceState();
}

class _AppProbePreferenceState extends State<AppProbePreference> {
  bool _probing = false;
  Map<String, dynamic>? _result;
  String? _selectedModel;

  @override
  void initState() {
    super.initState();
    _result = widget.lastProbe;
    _selectedModel = _pickDefaultModel(_result);
  }

  @override
  void didUpdateWidget(covariant AppProbePreference oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.lastProbe != widget.lastProbe) {
      final fresh = widget.lastProbe;
      // Preserve user's model selection across external refreshes if still valid.
      final models = _modelsOf(fresh);
      if (_selectedModel == null || !models.contains(_selectedModel)) {
        _selectedModel = _pickDefaultModel(fresh);
      }
      _result = fresh;
    }
  }

  Future<void> _runProbe() async {
    if (_probing || !widget.enabled) return;
    setState(() => _probing = true);
    try {
      final result = await widget.onProbe();
      if (!mounted) return;
      final models = _modelsOf(result);
      final previous = _selectedModel;
      setState(() {
        _result = result;
        if (previous != null && models.contains(previous)) {
          _selectedModel = previous;
        } else {
          _selectedModel = _pickDefaultModel(result);
        }
        _probing = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _probing = false);
      // The API client already surfaces the error via AppErrors.showSnack
      // in the calling page; keep the tile silent.
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppPreferenceTile(
          title: l10n.aiKeyProbeTitle,
          icon: Icons.network_check_rounded,
          enabled: widget.enabled && !_probing,
          accentColor: widget.accentColor,
          onTap: widget.enabled && !_probing ? _runProbe : null,
          leading: _probing
              ? SizedBox(
                  width: 24,
                  height: 24,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: widget.accentColor ?? Theme.of(context).colorScheme.primary,
                  ),
                )
              : Icon(
                  Icons.network_check_rounded,
                  size: 24,
                  color: widget.enabled
                      ? (widget.accentColor ??
                          Theme.of(context).colorScheme.onSurfaceVariant)
                      : Theme.of(context).disabledColor,
                ),
          subtitle: _result == null
              ? Text(l10n.aiKeyProbeNeverRun)
              : _buildResultSubtitle(context, l10n, _result!),
          trailing: _probing
              ? null
              : Icon(
                  Icons.play_circle_outline_rounded,
                  color: widget.enabled
                      ? (widget.accentColor ??
                          Theme.of(context).colorScheme.onSurfaceVariant)
                      : Theme.of(context).disabledColor,
                ),
        ),
        if (widget.enabled && _hasModels(_result) && !_probing)
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg,
              0,
              AppSpacing.lg,
              AppSpacing.sm,
            ),
            child: _buildModelPicker(context, l10n),
          ),
      ],
    );
  }

  Widget _buildResultSubtitle(
    BuildContext context,
    AppLocalizations l10n,
    Map<String, dynamic> result,
  ) {
    final status = result['status'] as String? ?? 'none';
    final latency = result['latency_ms'];
    final at = result['checked_at'] as String? ?? '';
    final errorCode = result['error_code'] as String?;
    final errorMsg = result['error_message'] as String?;
    final modelCount = _modelsOf(result).length;

    final severity = _severityFor(status);
    final color = _statusColor(context, severity);
    final label = _statusLabel(l10n, status);

    final parts = <String>[label];
    if (latency != null) parts.add('${latency}ms');
    if (modelCount > 0) parts.add(l10n.aiKeyProbeModelsCount);
    if (at.isNotEmpty) parts.add(at);
    final head = parts.join(' · ');

    final lines = <TextSpan>[TextSpan(text: head)];
    if (status == 'error' || status == 'unavailable') {
      if (errorCode != null && errorCode.isNotEmpty) {
        lines.add(const TextSpan(text: '\n'));
        lines.add(TextSpan(
          text: '${l10n.aiKeyProbeErrorCode}: $errorCode',
          style: Theme.of(context).textTheme.bodySmall,
        ));
      }
      if (errorMsg != null && errorMsg.isNotEmpty) {
        lines.add(const TextSpan(text: '\n'));
        lines.add(TextSpan(
          text: '${l10n.aiKeyProbeErrorMessage}: $errorMsg',
          style: Theme.of(context).textTheme.bodySmall,
        ));
      }
    }

    return RichText(
      text: TextSpan(
        style: Theme.of(context).textTheme.bodySmall?.copyWith(color: color),
        children: lines,
      ),
    );
  }

  Widget _buildModelPicker(BuildContext context, AppLocalizations l10n) {
    final models = _modelsOf(_result);
    return Column(
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
    );
  }

  bool _hasModels(Map<String, dynamic>? result) {
    return _modelsOf(result).isNotEmpty;
  }

  List<String> _modelsOf(Map<String, dynamic>? result) {
    if (result == null) return const [];
    final models = result['models'];
    if (models is List) {
      return models.whereType<String>().toList(growable: false);
    }
    return const [];
  }

  String? _pickDefaultModel(Map<String, dynamic>? result) {
    if (result == null) return null;
    final def = result['default_model'] as String?;
    if (def != null && def.isNotEmpty) return def;
    final models = _modelsOf(result);
    return models.isNotEmpty ? models.first : null;
  }

  AppStatusSeverity _severityFor(String status) {
    switch (status) {
      case 'ok':
        return AppStatusSeverity.success;
      case 'error':
        return AppStatusSeverity.error;
      case 'unavailable':
        return AppStatusSeverity.warning;
      default:
        return AppStatusSeverity.info;
    }
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
      default:
        return l10n.aiKeyProbeStatusNone;
    }
  }
}
