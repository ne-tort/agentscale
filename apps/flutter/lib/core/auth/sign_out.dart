import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/features/employee/login_page.dart';

/// Ends Auth Service session when possible, then clears local state and returns to sign-in.
Future<void> signOut(BuildContext context) async {
  await tokenSession.remoteLogout();
  await tokenSession.clear();
  if (!context.mounted) return;
  Navigator.of(context).pushAndRemoveUntil(
    MaterialPageRoute<void>(builder: (_) => const LoginPage()),
    (_) => false,
  );
}
