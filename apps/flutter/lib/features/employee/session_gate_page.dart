import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/admin_shell.dart';
import 'package:prodavan/features/employee/contour_selector_page.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';
import 'package:prodavan/features/employee/login_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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

  Future<void> _navigateAfterMe(Map<String, dynamic> me) async {
    if (!mounted) return;
    final contours = me['contours'];
    final memberships = me['employee']?['memberships'];
    final isPlatformAdmin = contours is List && contours.contains('platform_admin');
    final hasMemberships = memberships is List && memberships.isNotEmpty;
    final companyId = tokenSession.companyId;
    if (isPlatformAdmin && !hasMemberships) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => const AdminShell()),
      );
      return;
    }
    if (companyId == null && memberships is List && memberships.length > 1) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => ContourSelectorPage(me: me)),
      );
      return;
    }
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const CabinetListPage()),
    );
  }

  Future<void> _restore() async {
    final restored = await tokenSession.restore();
    if (restored == null) {
      if (!mounted) return;
      setState(() => _checking = false);
      return;
    }
    try {
      // Proactive refresh if access is near expiry / expired.
      await tokenSession.requireAccessToken();
      final me = await workContext.api.me();
      await _navigateAfterMe(me);
    } catch (_) {
      final refreshed = await tokenSession.refresh(force: true);
      if (refreshed) {
        try {
          final me = await workContext.api.me();
          await _navigateAfterMe(me);
          return;
        } catch (_) {}
      }
      await tokenSession.clear();
      if (!mounted) return;
      setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_checking) {
      return AppScaffold(
        title: Text(l10n.navProdavan),
        body: const Center(child: CircularProgressIndicator()),
      );
    }
    return const LoginPage();
  }
}
