import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/features/admin/admin_shell.dart';

/// Dev-only Platform Admin bearer entry (L04).
class DevAdminSessionPage extends StatefulWidget {
  const DevAdminSessionPage({super.key});

  @override
  State<DevAdminSessionPage> createState() => _DevAdminSessionPageState();
}

class _DevAdminSessionPageState extends State<DevAdminSessionPage> {
  final _baseCtrl = TextEditingController(text: adminContext.baseUrl);
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
      final me = await adminContext.api.me();
      final contours = me['contours'];
      final isAdmin = contours is List && contours.contains('platform_admin');
      if (!isAdmin) {
        adminContext.clear();
        setState(() => _error = 'Token must have platform_admin contour');
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
    return AppScaffold(
      title: const Text('Admin session'),
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AppTextField(controller: _baseCtrl, label: 'API base URL'),
            const SizedBox(height: AppSpacing.md),
            AppTextField(
              controller: _tokenCtrl,
              label: 'Bearer token (platform_admin JWT)',
            ),
            if (_error != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
            const SizedBox(height: AppSpacing.lg),
            AppButton(label: 'Continue', onPressed: _connect),
          ],
        ),
      ),
    );
  }
}
