import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Inline "Verify" preference tile for an AI key.
///
/// Tapping the row runs a key validity probe inline (no separate page). While
/// probing, the trailing shows a spinner; after completion the subtitle shows
/// the probe status (valid / invalid / unavailable) + latency. The "Models"
/// page link is shown separately by the parent page (only when the probe
/// returned a models list).
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

  @override
  void initState() {
    super.initState();
    _result = widget.lastProbe;
  }

  @override
  void didUpdateWidget(covariant AppProbePreference oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.lastProbe != widget.lastProbe && !_probing) {
      _result = widget.lastProbe;
    }
  }

  Future<void> _runProbe() async {
    if (_probing || !widget.enabled) return;
    setState(() => _probing = true);
    try {
      final result = await widget.onProbe();
      if (!mounted) return;
      setState(() {
        _result = result;
        _probing = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _probing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppPreferenceTile(
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
      subtitle: _buildSubtitle(context, l10n),
      trailing: _probing
          ? null
          : Icon(
              Icons.play_circle_outline_rounded,
              color: widget.enabled
                  ? (widget.accentColor ??
                      Theme.of(context).colorScheme.onSurfaceVariant)
                  : Theme.of(context).disabledColor,
            ),
    );
  }

  Widget _buildSubtitle(BuildContext context, AppLocalizations l10n) {
    final result = _result;
    if (result is! Map) {
      return Text(l10n.aiKeyProbeNeverRun);
    }
    final map = Map<String, dynamic>.from(result as Map);
    final status = map['status'] as String? ?? 'none';
    final latency = map['latency_ms'];
    final modelsCount = _modelsCount(map);

    final severity = _severityFor(status);
    final color = _statusColor(context, severity);
    final label = _statusLabel(l10n, status);

    final parts = <String>[label];
    if (latency != null) parts.add('${latency}ms');
    if (modelsCount > 0) parts.add(l10n.aiKeyProbeModelsCountValue(modelsCount));
    return Text(
      parts.join(' · '),
      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: color),
    );
  }

  int _modelsCount(Map<String, dynamic> result) {
    final models = result['models'];
    if (models is List) return models.length;
    return 0;
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
