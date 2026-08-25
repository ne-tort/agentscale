import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';

/// Boolean preference — saves immediately on toggle.
class AppSwitchPreference extends StatelessWidget {
  const AppSwitchPreference({
    super.key,
    required this.title,
    required this.value,
    required this.onChanged,
    this.enabled = true,
    this.icon,
    this.subtitle,
  });

  final String title;
  final bool value;
  final Future<void> Function(bool value) onChanged;
  final bool enabled;
  final IconData? icon;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    return AppPreferenceTile(
      title: title,
      icon: icon,
      enabled: enabled,
      subtitle: subtitle != null ? Text(subtitle!) : null,
      trailing: Switch.adaptive(
        value: value,
        onChanged: enabled
            ? (v) async {
                await onChanged(v);
              }
            : null,
      ),
    );
  }
}
