import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Inline "Verify" preference tile for an AI key.
///
/// Shown when the key has a secret. The subtitle shows the models count
/// (or "Models not received" on error), fetched once when the tile appears.
/// Tapping the row opens the models table page (onProbe) where each model
/// can be probed individually.
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

  /// Fires when the user taps "Verify" — opens the models table page.
  final Future<void> Function() onProbe;

  final Color? accentColor;

  @override
  State<AppProbePreference> createState() => _AppProbePreferenceState();
}

class _AppProbePreferenceState extends State<AppProbePreference> {
  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppPreferenceTile(
      title: l10n.aiKeyProbeTitle,
      icon: Icons.network_check_rounded,
      enabled: widget.enabled,
      accentColor: widget.accentColor,
      onTap: widget.enabled ? widget.onProbe : null,
      subtitle: _buildSubtitle(context, l10n),
      trailing: Icon(
        Icons.chevron_right_rounded,
        color: widget.enabled
            ? (widget.accentColor ??
                Theme.of(context).colorScheme.onSurfaceVariant)
            : Theme.of(context).disabledColor,
      ),
    );
  }

  Widget _buildSubtitle(BuildContext context, AppLocalizations l10n) {
    final probe = widget.lastProbe;
    if (probe is! Map) {
      return Text(l10n.aiKeyProbeModelsNotReceived);
    }
    final map = Map<String, dynamic>.from(probe as Map);
    final status = map['status'] as String? ?? 'none';
    if (status == 'none') {
      return Text(l10n.aiKeyProbeModelsNotReceived);
    }
    final models = map['models'];
    final count = models is List ? models.length : 0;
    if (status == 'ok' && count > 0) {
      return Text(l10n.aiKeyProbeModelsCountValue(count));
    }
    // Probe ran but no models returned (or failed) — show error/none.
    if (status == 'error' || status == 'unavailable') {
      return Text(l10n.aiKeyProbeModelsNotReceived,
          style: TextStyle(color: context.appColors.warning));
    }
    return Text(l10n.aiKeyProbeModelsNotReceived);
  }
}
