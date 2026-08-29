import 'package:flutter/material.dart';

import 'package:prodavan/core/settings/app_settings_controller.dart';
import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/post_login_navigation.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/config/api_base.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_card.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Unified username/password login via Prodavan Auth Service (never Keycloak).
class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();
  final _usernameFocus = FocusNode();
  final _passwordFocus = FocusNode();

  bool _loadingConfig = true;
  bool _connecting = false;
  bool _passwordLogin = false;
  bool _obscurePassword = true;

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  @override
  void dispose() {
    _usernameController.dispose();
    _passwordController.dispose();
    _usernameFocus.dispose();
    _passwordFocus.dispose();
    super.dispose();
  }

  Future<void> _loadConfig() async {
    setState(() => _loadingConfig = true);
    try {
      final cfg = await AuthConfigClient(baseUrl: ApiBase.value).fetch();
      if (!mounted) return;
      final mode = (cfg['auth_mode'] as String?)?.trim().toLowerCase();
      final features = cfg['features'];
      final enabled = mode == 'oidc' &&
          (features is! Map || features['password_login'] != false);
      setState(() {
        _passwordLogin = enabled;
        _loadingConfig = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _passwordLogin = false;
        _loadingConfig = false;
      });
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _submit() async {
    final username = _usernameController.text.trim();
    final password = _passwordController.text;
    if (username.isEmpty || password.isEmpty || _connecting || !_passwordLogin) {
      return;
    }

    setState(() => _connecting = true);
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
      setState(() => _connecting = false);
      AppErrors.showSnack(context, e);
    }
  }

  void _onPasswordSubmitted(String _) {
    if (_usernameController.text.trim().isNotEmpty) {
      _submit();
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final canSubmit = !_connecting &&
        _passwordLogin &&
        _usernameController.text.trim().isNotEmpty &&
        _passwordController.text.isNotEmpty;

    return Theme(
      data: AppTheme.loginPage(Theme.of(context), appSettings.themeMode),
      child: AppScaffold(
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
                      child: FocusTraversalGroup(
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
                            AppPreferenceTile(
                              title: l10n.authLogin,
                              icon: Icons.person_outline_rounded,
                              enabled: !_connecting,
                              subtitle: TextField(
                                controller: _usernameController,
                                focusNode: _usernameFocus,
                                enabled: !_connecting,
                                textInputAction: TextInputAction.next,
                                decoration: kBorderlessInputDecoration,
                                onChanged: (_) => setState(() {}),
                                onSubmitted: (_) => _passwordFocus.requestFocus(),
                              ),
                            ),
                            AppPreferenceTile(
                              title: l10n.authPassword,
                              icon: Icons.key_outlined,
                              enabled: !_connecting,
                              trailing: IconButton(
                                icon: Icon(
                                  _obscurePassword
                                      ? Icons.visibility_outlined
                                      : Icons.visibility_off_outlined,
                                ),
                                onPressed: _connecting
                                    ? null
                                    : () => setState(
                                          () => _obscurePassword = !_obscurePassword,
                                        ),
                              ),
                              subtitle: TextField(
                                controller: _passwordController,
                                focusNode: _passwordFocus,
                                enabled: !_connecting,
                                obscureText: _obscurePassword,
                                textInputAction: TextInputAction.done,
                                decoration: kBorderlessInputDecoration,
                                onChanged: (_) => setState(() {}),
                                onSubmitted: _onPasswordSubmitted,
                              ),
                            ),
                            if (_connecting)
                              const Padding(
                                padding: EdgeInsets.all(AppSpacing.lg),
                                child: Center(
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                ),
                              )
                            else if (canSubmit)
                              AppNavPreference(
                                title: l10n.authSignIn,
                                icon: Icons.login_rounded,
                                onTap: _submit,
                              ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
      ),
    );
  }
}
