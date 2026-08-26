import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/employee/login_page.dart';

/// Clears persisted session and in-memory contexts; returns to sign-in.
Future<void> signOut(BuildContext context) async {
  await sessionStore.clear();
  workContext.clear();
  adminContext.clear();
  if (!context.mounted) return;
  Navigator.of(context).pushAndRemoveUntil(
    MaterialPageRoute<void>(builder: (_) => const LoginPage()),
    (_) => false,
  );
}
