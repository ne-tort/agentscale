import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/features/employee/contour_selector_page.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';

/// Unified login — test JWT paste or OIDC stub (L01/L05).
class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _formKey = GlobalKey<FormState>();
  final _baseCtrl = TextEditingController(text: workContext.baseUrl);
  final _tokenCtrl = TextEditingController();
  bool _loadingConfig = true;
  bool _connecting = false;
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
        _error = 'Auth config unavailable — using test mode. $e';
      });
    }
  }

  Future<void> _openKeycloak() async {
    final oidc = _authConfig?['oidc'];
    if (oidc is! Map<String, dynamic>) return;
    final authEndpoint = oidc['authorization_endpoint'] as String?;
    final clientId = oidc['client_id'] as String?;
    if (authEndpoint == null || clientId == null) return;

    final uri = Uri.parse(authEndpoint).replace(
      queryParameters: {
        'client_id': clientId,
        'response_type': 'token',
        'scope': 'openid profile email',
        'redirect_uri': 'prodavan://oauth/callback',
      },
    );
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && mounted) {
      setState(() => _error = 'Could not open browser for Keycloak login');
    }
  }

  Future<void> _connect() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _connecting = true;
      _error = null;
    });
    try {
      final baseUrl = _baseCtrl.text.trim();
      final token = _tokenCtrl.text.trim();
      workContext.setSession(baseUrl: baseUrl, bearerToken: token);
      final me = await workContext.api.me();
      final memberships = me['employee']?['memberships'];
      String? companyId;
      if (memberships is List && memberships.length == 1) {
        companyId = memberships.first['company_id'] as String?;
      }
      await sessionStore.save(baseUrl: baseUrl, bearerToken: token, companyId: companyId);
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
                    isOidc ? 'OIDC mode — paste access token after Keycloak login' : 'Test mode — paste JWT',
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
                  const SizedBox(height: AppSpacing.md),
                  AppTextField(
                    controller: _tokenCtrl,
                    label: 'Bearer access token',
                    enabled: !_connecting,
                    validator: (v) => (v ?? '').trim().isEmpty ? 'Required' : null,
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: AppSpacing.sm),
                    Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                  ],
                  const SizedBox(height: AppSpacing.lg),
                  if (isOidc)
                    AppButton(
                      label: 'Open Keycloak login',
                      expanded: false,
                      onPressed: _connecting ? null : _openKeycloak,
                    ),
                  if (isOidc) const SizedBox(height: AppSpacing.sm),
                  AppButton(
                    label: _connecting ? 'Connecting…' : 'Continue',
                    onPressed: _connecting ? null : _connect,
                  ),
                ],
              ),
            ),
    );
  }
}
