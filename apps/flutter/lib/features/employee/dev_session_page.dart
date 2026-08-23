import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';

/// Dev-only bearer token entry until AppAuth (L01 cutover).
class DevSessionPage extends StatefulWidget {
  const DevSessionPage({super.key});

  @override
  State<DevSessionPage> createState() => _DevSessionPageState();
}

class _DevSessionPageState extends State<DevSessionPage> {
  final _baseCtrl = TextEditingController(text: workContext.baseUrl);
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
    workContext.setSession(
      baseUrl: _baseCtrl.text.trim(),
      bearerToken: _tokenCtrl.text.trim(),
    );
    try {
      final me = await workContext.api.me();
      final memberships = me['employee']?['memberships'];
      if (memberships is List && memberships.isNotEmpty) {
        workContext.companyId = memberships.first['company_id'] as String?;
      }
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => const CabinetListPage()),
      );
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Dev session'),
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AppTextField(controller: _baseCtrl, label: 'API base URL'),
            const SizedBox(height: AppSpacing.md),
            AppTextField(controller: _tokenCtrl, label: 'Bearer token (AUTH_MODE=test JWT)'),
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
