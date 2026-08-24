import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/oidc_auth_service.dart';
import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/features/employee/contour_selector_page.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';

/// Unified login — OIDC PKCE (AppAuth / desktop loopback) or test JWT paste.
class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _formKey = GlobalKey<FormState>();
  final _baseCtrl = TextEditingController(text: WorkContext.defaultBaseUrl);
  final _tokenCtrl = TextEditingController();
  bool _loadingConfig = true;
  bool _connecting = false;
  bool _showTestPaste = false;
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
        _showTestPaste = cfg['auth_mode'] != 'oidc';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _authConfig = {'auth_mode': 'test', 'oidc': null};
        _loadingConfig = false;
        _showTestPaste = true;
        _error = 'Auth config unavailable — using test mode. $e';
      });
    }
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
    final mode = _authConfig?['auth_mode'] as String? ?? 'test';
    final isOidc = mode == 'oidc';
    return AppScaffold(
      title: const Text('Sign in'),
      body: _loadingConfig
          ? const Center(child: CircularProgressIndicator())
          : Padding(
              padding: const EdgeInsets.all(AppSpacing.lg),
              child: AppForm(
                formKey: _formKey,
                children: [
                  Text(
                    isOidc
                        ? 'OIDC — PKCE via Keycloak (mobile AppAuth, desktop browser loopback)'
                        : 'Test mode — paste JWT from CI or dev token helper',
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                  const SizedBox(height: AppSpacing.md),
                  AppTextField(
                    controller: _baseCtrl,
                    label: 'API base URL',
                    enabled: !_connecting,
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  TextButton(onPressed: _connecting ? null : _loadConfig, child: const Text('Reload auth config')),
                  if (_error != null) ...[
                    const SizedBox(height: AppSpacing.sm),
                    Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                  ],
                  const SizedBox(height: AppSpacing.lg),
                  if (isOidc)
                    AppButton(
                      label: _connecting ? 'Opening login…' : 'Sign in with Keycloak',
                      onPressed: _connecting ? null : _signInOidc,
                    ),
                  if (isOidc) ...[
                    const SizedBox(height: AppSpacing.sm),
                    TextButton(
                      onPressed: _connecting ? null : () => setState(() => _showTestPaste = !_showTestPaste),
                      child: Text(_showTestPaste ? 'Hide test token paste' : 'Advanced: paste token'),
                    ),
                  ],
                  if (!isOidc || _showTestPaste) ...[
                    const SizedBox(height: AppSpacing.md),
                    AppTextField(
                      controller: _tokenCtrl,
                      label: 'Bearer access token',
                      enabled: !_connecting,
                      validator: (v) => (v ?? '').trim().isEmpty ? 'Required' : null,
                    ),
                    const SizedBox(height: AppSpacing.sm),
                    AppButton(
                      label: _connecting ? 'Connecting…' : 'Continue with token',
                      expanded: false,
                      onPressed: _connecting ? null : _connectTest,
                    ),
                  ],
                ],
              ),
            ),
    );
  }
}
