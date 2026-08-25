import 'package:flutter/material.dart';

import 'package:prodavan/features/settings/settings_page.dart';

/// Single entry for opening app Settings (language + theme).
Future<void> openAppSettings(BuildContext context) {
  return Navigator.of(context).push<void>(
    MaterialPageRoute<void>(builder: (_) => const SettingsPage()),
  );
}
