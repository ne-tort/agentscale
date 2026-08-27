import 'package:flutter/material.dart';

import 'package:prodavan/l10n/app_localizations.dart';

/// Small online/offline dot for entity tables (metrics BC presence).
class AppOnlineIndicator extends StatelessWidget {
  const AppOnlineIndicator({
    super.key,
    required this.online,
    this.size = 10,
  });

  final bool online;
  final double size;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final color = online ? Colors.green : Theme.of(context).disabledColor;
    return Tooltip(
      message: online ? l10n.commonOnline : l10n.commonOffline,
      child: Icon(Icons.circle, size: size, color: color),
    );
  }
}
