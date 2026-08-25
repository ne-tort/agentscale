import 'package:flutter/material.dart';

import 'package:prodavan/core/config/api_base.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/admin_shell.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Dev-only Platform Admin bearer entry (L04).
class DevAdminSessionPage extends StatefulWidget {
  const DevAdminSessionPage({super.key});

  @override
  State<DevAdminSessionPage> createState() => _DevAdminSessionPageState();
}

class _DevAdminSessionPageState extends State<DevAdminSessionPage> {
  final _baseCtrl = TextEditingController(text: ApiBase.value);
  final _tokenCtrl = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _baseCtrl.dispose();
    _tokenCtrl.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    setState(() => _error = null);
    adminContext.setSession(
      baseUrl: _baseCtrl.text.trim(),
      bearerToken: _tokenCtrl.text.trim(),
    );
    try {
      final l10n = AppLocalizations.of(context);
      final me = await adminContext.api.me();
      final contours = me['contours'];
      final isAdmin = contours is List && contours.contains('platform_admin');
      if (!isAdmin) {
        adminContext.clear();
        setState(() => _error = l10n.devTokenMustHavePlatformAdmin);
        return;
      }
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => const AdminShell()),
      );
    } catch (e) {
      adminContext.clear();
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.devAdminSession),
      body: Padding(
        padding: EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _baseCtrl,
              decoration: InputDecoration(labelText: l10n.commonApiBaseUrl),
            ),
            const SizedBox(height: AppSpacing.md),
            TextField(
              controller: _tokenCtrl,
              decoration: InputDecoration(labelText: l10n.devBearerTokenPlatformAdmin),
            ),
            if (_error != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(_error!, style: TextStyle(color: context.appColors.danger)),
            ],
            const SizedBox(height: AppSpacing.lg),
            AppButton(label: l10n.commonContinueAction, onPressed: _connect),
          ],
        ),
      ),
    );
  }
}
