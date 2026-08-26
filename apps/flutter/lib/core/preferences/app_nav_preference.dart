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
  });

  final String title;
  final IconData icon;
  final VoidCallback onTap;
  final Widget? subtitle;
  final Color? accentColor;

  @override
  Widget build(BuildContext context) {
    return AppPreferenceTile(
      title: title,
      icon: icon,
      subtitle: subtitle,
      accentColor: accentColor,
      trailing: const AppTrailingChevron(),
      onTap: onTap,
    );
  }
}
