import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';

/// Hub row — navigates to a sub-page (Hiddify SettingsSection pattern).
class AppNavPreference extends StatelessWidget {
  const AppNavPreference({
    super.key,
    required this.title,
    required this.icon,
    required this.onTap,
    this.subtitle,
  });

  final String title;
  final IconData icon;
  final VoidCallback onTap;
  final Widget? subtitle;

  @override
  Widget build(BuildContext context) {
    return AppPreferenceTile(
      title: title,
      icon: icon,
      subtitle: subtitle,
      trailing: const Icon(Icons.chevron_right_rounded, size: 22),
      onTap: onTap,
    );
  }
}
