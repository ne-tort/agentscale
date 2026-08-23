import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
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
      if (!mounted) return;
      final memberships = me['employee']?['memberships'];
      if (stored.companyId == null && memberships is List && memberships.length > 1) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute<void>(builder: (_) => ContourSelectorPage(me: me)),
        );
        return;
      }
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => const CabinetListPage()),
      );
    } catch (_) {
      await sessionStore.clear();
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
