import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/oidc_auth_service.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/features/employee/login_page.dart';

/// Ends Keycloak session when possible, then clears local state and returns to sign-in.
Future<void> signOut(BuildContext context) async {
  final current = tokenSession.session;
  if (current != null) {
    try {
      final cfg = await AuthConfigClient(baseUrl: current.baseUrl).fetch();
      final mode = (cfg['auth_mode'] as String?)?.trim().toLowerCase();
      final oidc = cfg['oidc'];
      if (mode == 'oidc' && oidc is Map<String, dynamic>) {
        await oidcAuthService.signOut(
          oidc: oidc,
          refreshToken: current.refreshToken,
          accessToken: current.accessToken,
          idToken: current.idToken,
        );
      }
    } catch (_) {
      // Still clear local session if discovery / revoke / end-session fails.
    }
  }

  await tokenSession.clear();
  if (!context.mounted) return;
  Navigator.of(context).pushAndRemoveUntil(
    MaterialPageRoute<void>(builder: (_) => const LoginPage()),
    (_) => false,
  );
}
