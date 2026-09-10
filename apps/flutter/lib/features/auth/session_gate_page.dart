import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_session_policy.dart';
import 'package:prodavan/core/auth/post_login_navigation.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/settings/app_settings_controller.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_card.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/features/auth/login_page.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
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
      setState(() => _checking = true);
    }

    final restored = await tokenSession.restore();
    if (restored == null) {
      if (!mounted) return;
      setState(() {
        _checking = false;
        _offline = false;
      });
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
    if (_checking && !_offline) {
      return const AppScaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    if (_offline) {
      return Theme(
        data: AppTheme.loginPage(Theme.of(context), appSettings.themeMode),
        child: AppScaffold(
          expandBody: true,
          body: Center(
            child: SingleChildScrollView(
              padding: EdgeInsets.all(AppSpacing.lg),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: AppCard(
                  padding: EdgeInsets.symmetric(
                    horizontal: AppSpacing.sm,
                    vertical: AppSpacing.md,
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: AppSpacing.sm,
                        ),
                        child: AppSectionHeader(
                          title: l10n.sessionRestoreOffline,
                          trailing: AppIconButton(
                            icon: Icons.settings_outlined,
                            tooltip: l10n.settings,
                            onPressed: () => openAppSettings(context),
                          ),
                        ),
                      ),
                      if (_checking)
                        const Padding(
                          padding: EdgeInsets.all(AppSpacing.lg),
                          child: Center(
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        )
                      else
                        AppNavPreference(
                          title: l10n.commonReload,
                          icon: Icons.refresh_rounded,
                          onTap: _restore,
                        ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }
    return const LoginPage();
  }
}
