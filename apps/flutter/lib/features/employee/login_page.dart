import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/post_login_navigation.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/config/api_base.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
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
  bool _obscured = true;
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
    final scheme = Theme.of(context).colorScheme;
    return AppScaffold(
      expandBody: true,
      actions: [
        IconButton(
          icon: const Icon(Icons.settings_outlined),
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
                      DecoratedBox(
                        decoration: BoxDecoration(
                          border: Border(
                            bottom: BorderSide(color: scheme.outlineVariant),
                          ),
                        ),
                        child: TextField(
                          controller: _userCtrl,
                          focusNode: _userFocus,
                          enabled: !_connecting,
                          autofillHints: const [AutofillHints.username],
                          textInputAction: TextInputAction.next,
                          onSubmitted: (_) => _passFocus.requestFocus(),
                          decoration: kBorderlessInputDecoration.copyWith(
                            contentPadding: const EdgeInsets.symmetric(vertical: 14),
                          ),
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ),
                      SizedBox(height: AppSpacing.sm),
                      DecoratedBox(
                        decoration: BoxDecoration(
                          border: Border(
                            bottom: BorderSide(color: scheme.outlineVariant),
                          ),
                        ),
                        child: Row(
                          children: [
                            Expanded(
                              child: TextField(
                                controller: _passCtrl,
                                focusNode: _passFocus,
                                enabled: !_connecting,
                                obscureText: _obscured,
                                autofillHints: const [AutofillHints.password],
                                textInputAction: TextInputAction.go,
                                onSubmitted: (_) => _submit(),
                                decoration: kBorderlessInputDecoration.copyWith(
                                  contentPadding:
                                      const EdgeInsets.symmetric(vertical: 14),
                                ),
                                style: Theme.of(context).textTheme.titleMedium,
                              ),
                            ),
                            IconButton(
                              icon: Icon(
                                _obscured
                                    ? Icons.visibility_outlined
                                    : Icons.visibility_off_outlined,
                              ),
                              onPressed: _connecting
                                  ? null
                                  : () => setState(() => _obscured = !_obscured),
                            ),
                          ],
                        ),
                      ),
                      SizedBox(height: AppSpacing.lg),
                      Align(
                        alignment: Alignment.centerRight,
                        child: FilledButton(
                          onPressed: _connecting || _authConfig?['oidc'] == null
                              ? null
                              : _submit,
                          child: _connecting
                              ? const SizedBox(
                                  width: 22,
                                  height: 22,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Icon(Icons.arrow_forward_rounded),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
    );
  }
}
