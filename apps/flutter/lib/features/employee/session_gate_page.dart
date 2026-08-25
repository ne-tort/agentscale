import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/oidc_auth_service.dart';
import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/admin_shell.dart';
import 'package:prodavan/features/employee/contour_selector_page.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';
import 'package:prodavan/features/employee/login_page.dart';

/// App entry with session restore (L01/L05).
class SessionGatePage extends StatefulWidget {
  const SessionGatePage({super.key});

  @override
  State<SessionGatePage> createState() => _SessionGatePageState();
}

class _SessionGatePageState extends State<SessionGatePage> {
  bool _checking = true;

  @override
  void initState() {
    super.initState();
    _restore();
  }

  Future<void> _navigateAfterMe(Map<String, dynamic> me, StoredSession stored) async {
    if (!mounted) return;
    final contours = me['contours'];
    final memberships = me['employee']?['memberships'];
    final isPlatformAdmin = contours is List && contours.contains('platform_admin');
    final hasMemberships = memberships is List && memberships.isNotEmpty;
    if (isPlatformAdmin && !hasMemberships) {
      adminContext.setSession(
        baseUrl: stored.baseUrl,
        bearerToken: workContext.bearerToken,
      );
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => const AdminShell()),
      );
      return;
    }
    if (stored.companyId == null && memberships is List && memberships.length > 1) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => ContourSelectorPage(me: me)),
      );
      return;
    }
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const CabinetListPage()),
    );
  }

  Future<bool> _tryRefresh(StoredSession stored) async {
    final refresh = stored.refreshToken;
    if (refresh == null || refresh.isEmpty) return false;
    try {
      final cfg = await AuthConfigClient(baseUrl: stored.baseUrl).fetch();
      final oidc = cfg['oidc'];
      if (oidc is! Map<String, dynamic>) return false;
      final result = await oidcAuthService.refresh(oidc: oidc, refreshToken: refresh);
      if (result == null) return false;
      workContext.setSession(baseUrl: stored.baseUrl, bearerToken: result.accessToken);
      await sessionStore.save(
        baseUrl: stored.baseUrl,
        bearerToken: result.accessToken,
        refreshToken: result.refreshToken ?? refresh,
        companyId: stored.companyId,
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> _restore() async {
    final stored = await sessionStore.load();
    if (stored == null) {
      if (!mounted) return;
      setState(() => _checking = false);
      return;
    }
    workContext.setSession(baseUrl: stored.baseUrl, bearerToken: stored.bearerToken);
    workContext.companyId = stored.companyId;
    try {
      final me = await workContext.api.me();
      await _navigateAfterMe(me, stored);
    } catch (_) {
      if (await _tryRefresh(stored)) {
        try {
          final me = await workContext.api.me();
          await _navigateAfterMe(me, stored);
          return;
        } catch (_) {}
      }
      await sessionStore.clear();
      workContext.clear();
      if (!mounted) return;
      setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_checking) {
      return const AppScaffold(
        title: Text('Prodavan'),
        body: Center(child: CircularProgressIndicator()),
      );
    }
    return const LoginPage();
  }
}
