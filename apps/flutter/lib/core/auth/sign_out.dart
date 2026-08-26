import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/oidc_auth_service.dart';
import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/employee/login_page.dart';

/// Ends Keycloak session when possible, then clears local state and returns to sign-in.
Future<void> signOut(BuildContext context) async {
  final stored = await sessionStore.load();
  if (stored != null) {
    try {
      final cfg = await AuthConfigClient(baseUrl: stored.baseUrl).fetch();
      final mode = (cfg['auth_mode'] as String?)?.trim().toLowerCase();
      final oidc = cfg['oidc'];
      if (mode == 'oidc' && oidc is Map<String, dynamic>) {
        await oidcAuthService.signOut(
          oidc: oidc,
          refreshToken: stored.refreshToken,
          accessToken: stored.bearerToken,
          idToken: stored.idToken,
        );
      }
    } catch (_) {
      // Still clear local session if discovery / revoke / end-session fails.
    }
  }

  await sessionStore.clear();
  workContext.clear();
  adminContext.clear();
  if (!context.mounted) return;
  Navigator.of(context).pushAndRemoveUntil(
    MaterialPageRoute<void>(builder: (_) => const LoginPage()),
    (_) => false,
  );
}
