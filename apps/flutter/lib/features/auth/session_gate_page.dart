import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_session_policy.dart';
import 'package:prodavan/core/auth/post_login_navigation.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/auth/login_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// App entry with session restore.
class SessionGatePage extends StatefulWidget {
  const SessionGatePage({super.key});

  @override
  State<SessionGatePage> createState() => _SessionGatePageState();
}

class _SessionGatePageState extends State<SessionGatePage> {
  bool _checking = true;
  bool _offline = false;

  @override
  void initState() {
    super.initState();
    _restore();
  }

  Future<void> _restore() async {
    if (mounted) {
      setState(() {
        _checking = true;
        _offline = false;
      });
    }

    final restored = await tokenSession.restore();
    if (restored == null) {
      if (!mounted) return;
      setState(() => _checking = false);
      return;
    }

    late Object failure;
    try {
      await _completeLogin();
      return;
    } catch (e) {
      failure = e;
    }

    var refreshAttempted = false;
    if (shouldAttemptRefresh(failure, session: tokenSession.session)) {
      refreshAttempted = true;
      final refreshErr = await tokenSession.refreshWithError(force: true);
      if (refreshErr == null) {
        try {
          await _completeLogin();
          return;
        } catch (e) {
          failure = e;
        }
      } else {
        failure = refreshErr;
      }
    }

    if (shouldClearSession(
      error: failure,
      session: tokenSession.session,
      refreshAttempted: refreshAttempted,
    )) {
      await tokenSession.clear();
      if (!mounted) return;
      setState(() {
        _checking = false;
        _offline = false;
      });
      return;
    }

    if (!mounted) return;
    setState(() {
      _checking = false;
      _offline = true;
    });
  }

  Future<void> _completeLogin() async {
    await tokenSession.requireAccessToken();
    final me = await workContext.api.me();
    if (!mounted) return;
    await navigateAfterMe(context, me);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_checking) {
      return const AppScaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    if (_offline) {
      return AppScaffold(
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.cloud_off_outlined,
                  size: 48,
                  color: Theme.of(context).colorScheme.outline,
                ),
                const SizedBox(height: AppSpacing.md),
                Text(
                  l10n.sessionRestoreOffline,
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: AppSpacing.sm),
                Text(
                  l10n.errorNetwork,
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: AppSpacing.lg),
                FilledButton(
                  onPressed: _restore,
                  child: Text(l10n.commonRetry),
                ),
              ],
            ),
          ),
        ),
      );
    }
    return const LoginPage();
  }
}
