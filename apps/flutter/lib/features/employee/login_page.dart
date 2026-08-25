import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/oidc_auth_service.dart';
import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/config/api_base.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/features/admin/admin_shell.dart';
import 'package:prodavan/features/employee/contour_selector_page.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Unified login — OIDC PKCE or AUTH_MODE=test one-click personas.
class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _formKey = GlobalKey<FormState>();
  final _baseCtrl = TextEditingController(text: ApiBase.value);
  final _tokenCtrl = TextEditingController();
  bool _loadingConfig = true;
  bool _connecting = false;
  bool _showAdvanced = false;
  String? _error;
  Map<String, dynamic>? _authConfig;

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  @override
  void dispose() {
    _baseCtrl.dispose();
    _tokenCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadConfig() async {
    setState(() {
      _loadingConfig = true;
      _error = null;
    });
    try {
      final cfg = await AuthConfigClient(baseUrl: _baseCtrl.text.trim()).fetch();
      if (!mounted) return;
      setState(() {
        _authConfig = cfg;
        _loadingConfig = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _authConfig = {'auth_mode': 'test', 'oidc': null};
        _loadingConfig = false;
        _error = AppLocalizations.of(context).authConfigUnavailableTestMode('$e');
      });
    }
  }

  Future<void> _navigateAfterMe(Map<String, dynamic> me) async {
    final contours = me['contours'];
    final memberships = me['employee']?['memberships'];
    final isPlatformAdmin = contours is List && contours.contains('platform_admin');
    final hasMemberships = memberships is List && memberships.isNotEmpty;

    if (isPlatformAdmin && !hasMemberships) {
      adminContext.setSession(
        baseUrl: workContext.baseUrl,
        bearerToken: workContext.bearerToken,
      );
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => const AdminShell()),
      );
      return;
    }

    if (!mounted) return;
    if (memberships is List && memberships.length > 1) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => ContourSelectorPage(me: me)),
      );
      return;
    }
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const CabinetListPage()),
    );
  }

  Future<void> _finishSession({
    required String baseUrl,
    required String token,
    String? refreshToken,
  }) async {
    workContext.setSession(baseUrl: baseUrl, bearerToken: token);
    final me = await workContext.api.me();
    final memberships = me['employee']?['memberships'];
    String? companyId;
    if (memberships is List && memberships.length == 1) {
      companyId = memberships.first['company_id'] as String?;
    }
    await sessionStore.save(
      baseUrl: baseUrl,
      bearerToken: token,
      refreshToken: refreshToken,
      companyId: companyId,
    );
    if (companyId != null) workContext.companyId = companyId;
    await _navigateAfterMe(me);
  }

  Future<void> _signInOidc() async {
    final oidc = _authConfig?['oidc'];
    if (oidc is! Map<String, dynamic>) return;

    setState(() {
      _connecting = true;
      _error = null;
    });
    try {
      final result = await oidcAuthService.signIn(oidc);
      await _finishSession(
        baseUrl: _baseCtrl.text.trim(),
        token: result.accessToken,
        refreshToken: result.refreshToken,
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _connecting = false;
      });
    }
  }

  Future<void> _signInTestPersona(String persona) async {
    final l10n = AppLocalizations.of(context);
    setState(() {
      _connecting = true;
      _error = null;
    });
    try {
      final body = await AuthConfigClient(baseUrl: _baseCtrl.text.trim()).testLogin(persona: persona);
      final token = body['access_token'] as String?;
      if (token == null || token.isEmpty) {
        throw Exception(l10n.authNoAccessTokenInTestLogin);
      }
      await _finishSession(baseUrl: _baseCtrl.text.trim(), token: token);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _connecting = false;
      });
    }
  }

  Future<void> _connectTest() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _connecting = true;
      _error = null;
    });
    try {
      await _finishSession(
        baseUrl: _baseCtrl.text.trim(),
        token: _tokenCtrl.text.trim(),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _connecting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final mode = _authConfig?['auth_mode'] as String? ?? 'test';
    final isOidc = mode == 'oidc';
    return AppScaffold(
      title: Text(l10n.authSignIn),
      actions: [
        IconButton(
          icon: const Icon(Icons.settings_outlined),
          tooltip: l10n.settings,
          onPressed: () => openAppSettings(context),
        ),
      ],
      body: _loadingConfig
          ? const Center(child: CircularProgressIndicator())
          : Padding(
              padding: EdgeInsets.all(AppSpacing.lg),
              child: AppForm(
                formKey: _formKey,
                children: [
                  Text(
                    isOidc ? l10n.authOidcModeHint : l10n.authDevTestModeHint,
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                  const SizedBox(height: AppSpacing.md),
                  if (_error != null) ...[
                    Text(_error!, style: TextStyle(color: context.appColors.danger)),
                    const SizedBox(height: AppSpacing.sm),
                  ],
                  if (isOidc)
                    AppButton(
                      label: _connecting ? l10n.authOpeningLogin : l10n.authSignInWithKeycloak,
                      onPressed: _connecting ? null : _signInOidc,
                    ),
                  if (!isOidc) ...[
                    AppButton(
                      label: _connecting ? l10n.authSigningIn : l10n.authContinueAsDemoEmployee,
                      onPressed: _connecting ? null : () => _signInTestPersona('demo_employee'),
                    ),
                    const SizedBox(height: AppSpacing.sm),
                    AppButton(
                      label: _connecting ? l10n.authSigningIn : l10n.authContinueAsPlatformAdmin,
                      variant: AppButtonVariant.outlined,
                      onPressed: _connecting ? null : () => _signInTestPersona('platform_admin'),
                    ),
                  ],
                  const SizedBox(height: AppSpacing.md),
                  TextButton(
                    onPressed: _connecting ? null : () => setState(() => _showAdvanced = !_showAdvanced),
                    child: Text(_showAdvanced ? l10n.authHideAdvanced : l10n.authAdvanced),
                  ),
                  if (_showAdvanced) ...[
                    const SizedBox(height: AppSpacing.sm),
                    AppTextField(
                      controller: _baseCtrl,
                      label: l10n.commonApiBaseUrl,
                      enabled: !_connecting,
                    ),
                    const SizedBox(height: AppSpacing.sm),
                    TextButton(onPressed: _connecting ? null : _loadConfig, child: Text(l10n.authReloadAuthConfig)),
                    if (isOidc) ...[
                      const SizedBox(height: AppSpacing.md),
                      AppTextField(
                        controller: _tokenCtrl,
                        label: l10n.authBearerAccessToken,
                        enabled: !_connecting,
                        validator: (v) => (v ?? '').trim().isEmpty ? l10n.commonRequired : null,
                      ),
                      const SizedBox(height: AppSpacing.sm),
                      AppButton(
                        label: _connecting ? l10n.authConnecting : l10n.authContinueWithToken,
                        expanded: false,
                        onPressed: _connecting ? null : _connectTest,
                      ),
                    ],
                    if (!isOidc) ...[
                      const SizedBox(height: AppSpacing.md),
                      AppTextField(
                        controller: _tokenCtrl,
                        label: l10n.authBearerAccessTokenPaste,
                        enabled: !_connecting,
                        validator: (v) => (v ?? '').trim().isEmpty ? l10n.commonRequired : null,
                      ),
                      const SizedBox(height: AppSpacing.sm),
                      AppButton(
                        label: _connecting ? l10n.authConnecting : l10n.authContinueWithToken,
                        expanded: false,
                        onPressed: _connecting ? null : _connectTest,
                      ),
                    ],
                  ],
                ],
              ),
            ),
    );
  }
}
