import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/post_login_navigation.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/config/api_base.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_password_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Unified username/password login via Keycloak ROPC.
class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _userCtrl = TextEditingController();
  final _passCtrl = TextEditingController();
  final _userFocus = FocusNode();
  final _passFocus = FocusNode();
  bool _loadingConfig = true;
  bool _connecting = false;
  Object? _error;
  Map<String, dynamic>? _authConfig;

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  @override
  void dispose() {
    _userCtrl.dispose();
    _passCtrl.dispose();
    _userFocus.dispose();
    _passFocus.dispose();
    super.dispose();
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
    final username = _userCtrl.text.trim();
    final password = _passCtrl.text;
    if (username.isEmpty || password.isEmpty || _connecting) return;

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
    final canSubmit = !_connecting && _authConfig?['oidc'] != null;
    return AppScaffold(
      title: Text(l10n.authSignIn),
      expandBody: true,
      actions: [
        AppIconButton(
          icon: Icons.settings_outlined,
          tooltip: l10n.settings,
          onPressed: () => openAppSettings(context),
        ),
      ],
      body: _loadingConfig
          ? const Center(child: CircularProgressIndicator())
          : Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 360),
                child: Padding(
                  padding: EdgeInsets.all(AppSpacing.lg),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      if (_error != null) ...[
                        Text(
                          AppErrors.localize(context, _error!),
                          style: TextStyle(color: context.appColors.danger),
                        ),
                        SizedBox(height: AppSpacing.md),
                      ],
                      AppTextField(
                        controller: _userCtrl,
                        focusNode: _userFocus,
                        label: l10n.authLogin,
                        enabled: !_connecting,
                        autofillHints: const [AutofillHints.username],
                        textInputAction: TextInputAction.next,
                        onFieldSubmitted: (_) => _passFocus.requestFocus(),
                      ),
                      SizedBox(height: AppSpacing.md),
                      AppPasswordField(
                        controller: _passCtrl,
                        focusNode: _passFocus,
                        label: l10n.authPassword,
                        enabled: !_connecting,
                        textInputAction: TextInputAction.go,
                        onFieldSubmitted: (_) => _submit(),
                      ),
                      SizedBox(height: AppSpacing.lg),
                      AppAsyncButton(
                        label: _connecting ? l10n.authSigningIn : l10n.authSignIn,
                        busy: _connecting,
                        onPressed: canSubmit ? _submit : null,
                      ),
                    ],
                  ),
                ),
              ),
            ),
    );
  }
}
