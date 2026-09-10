import 'package:flutter/material.dart';

import 'package:prodavan/features/settings/settings_page.dart';

/// Single entry for opening app Settings (language + theme).
///
/// [showSignOut] is false on login / offline gate so a stale session cannot
/// surface «Выйти» before the user is actually in the app shell.
Future<void> openAppSettings(
  BuildContext context, {
  bool showSignOut = true,
}) {
  return Navigator.of(context).push<void>(
    MaterialPageRoute<void>(
      builder: (_) => SettingsPage(showSignOut: showSignOut),
    ),
  );
}
