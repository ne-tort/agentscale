import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/post_login_navigation.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/config/api_base.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_card.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Unified username/password login via Keycloak ROPC.
///
/// Fields/button = preference kit + [AppButton] (same chrome as AI key secret).
class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  String _username = '';
  String _password = '';
  bool _loadingConfig = true;
  bool _connecting = false;
  Object? _error;
  Map<String, dynamic>? _authConfig;

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  Future<void> _loadConfig() async {
    setState(() {
      _loadingConfig = true;
      _error = null;
    });
    try {
      final cfg = await AuthConfigClient(baseUrl: ApiBase.value).fetch();
      if (!mounted) return;
      setState(() {
        _authConfig = cfg;
        _loadingConfig = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _authConfig = null;
        _loadingConfig = false;
        _error = e;
      });
    }
  }

  Future<void> _submit() async {
    final username = _username.trim();
    final password = _password;
    if (username.isEmpty || password.isEmpty || _connecting) return;
    if (_authConfig?['oidc'] == null) return;

    setState(() {
      _connecting = true;
      _error = null;
    });
    try {
      await tokenSession.loginWithPassword(
        baseUrl: ApiBase.value,
        username: username,
        password: password,
      );
      final me = await workContext.api.me();
      if (!mounted) return;
      await navigateAfterMe(context, me);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _connecting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final canSubmit = !_connecting &&
        _authConfig?['oidc'] != null &&
        _username.trim().isNotEmpty &&
        _password.isNotEmpty;

    return AppScaffold(
      expandBody: true,
      body: _loadingConfig
          ? const Center(child: CircularProgressIndicator())
          : Center(
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
                            title: l10n.authSignIn,
                            trailing: AppIconButton(
                              icon: Icons.settings_outlined,
                              tooltip: l10n.settings,
                              onPressed: () => openAppSettings(context),
                            ),
                          ),
                        ),
                        if (_error != null)
                          Padding(
                            padding: const EdgeInsets.fromLTRB(
                              AppSpacing.md,
                              0,
                              AppSpacing.md,
                              AppSpacing.sm,
                            ),
                            child: Text(
                              AppErrors.localize(context, _error!),
                              style: TextStyle(color: context.appColors.danger),
                            ),
                          ),
                        AppValuePreference<String>(
                          title: l10n.authLogin,
                          icon: Icons.person_outline_rounded,
                          value: _username,
                          enabled: !_connecting,
                          presentValue: (v) =>
                              v.isEmpty ? l10n.commonNotSet : v,
                          onSave: (v) async {
                            setState(() => _username = v.trim());
                          },
                        ),
                        AppValuePreference<String>(
                          title: l10n.authPassword,
                          icon: Icons.key_outlined,
                          value: _password,
                          obscureText: true,
                          enabled: !_connecting,
                          presentValue: (v) =>
                              v.isEmpty ? l10n.commonNotSet : '••••••••',
                          formatInputValue: (v) => v,
                          onSave: (v) async {
                            setState(() => _password = v);
                          },
                        ),
                        Padding(
                          padding: const EdgeInsets.fromLTRB(
                            AppSpacing.md,
                            AppSpacing.md,
                            AppSpacing.md,
                            AppSpacing.sm,
                          ),
                          child: AppAsyncButton(
                            label: _connecting
                                ? l10n.authSigningIn
                                : l10n.authSignIn,
                            busy: _connecting,
                            onPressed: canSubmit ? _submit : null,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
    );
  }
}
