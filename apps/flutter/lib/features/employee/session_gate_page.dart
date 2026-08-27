import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/post_login_navigation.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
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
    final restored = await tokenSession.restore();
    if (restored == null) {
      if (!mounted) return;
      setState(() => _checking = false);
      return;
    }
    try {
      await tokenSession.requireAccessToken();
      final me = await workContext.api.me();
      if (!mounted) return;
      await navigateAfterMe(context, me);
    } catch (_) {
      final refreshed = await tokenSession.refresh(force: true);
      if (refreshed) {
        try {
          final me = await workContext.api.me();
          if (!mounted) return;
          await navigateAfterMe(context, me);
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
    if (_checking) {
      return const AppScaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    return const LoginPage();
  }
}
