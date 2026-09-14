import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';

/// Hub row — navigates to a sub-page (Hiddify SettingsSection pattern).
class AppNavPreference extends StatelessWidget {
  const AppNavPreference({
    super.key,
    required this.title,
    required this.icon,
    required this.onTap,
    this.subtitle,
    this.accentColor,
    this.enabled = true,
    this.loading = false,
    this.loadingLabel,
    this.leading,
  });

  final String title;
  final IconData icon;
  final VoidCallback onTap;
  final Widget? subtitle;
  final Color? accentColor;
  final bool enabled;
  final bool loading;
  final String? loadingLabel;
  final Widget? leading;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final progressSubtitle = loading ? (loadingLabel ?? '') : null;
    final iconColor = enabled && !loading
        ? (accentColor ?? scheme.onSurfaceVariant)
        : Theme.of(context).disabledColor;

    return AppPreferenceTile(
      title: title,
      icon: icon,
      enabled: enabled && !loading,
      leading: loading
          ? SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: accentColor ?? scheme.primary,
              ),
            )
          : leading ??
              Icon(
                icon,
                size: 24,
                color: iconColor,
              ),
      subtitle: progressSubtitle != null && progressSubtitle.isNotEmpty
          ? Text(progressSubtitle)
          : subtitle,
      accentColor: accentColor,
      trailing: loading ? null : const AppTrailingChevron(),
      onTap: enabled && !loading ? onTap : null,
    );
  }
}
