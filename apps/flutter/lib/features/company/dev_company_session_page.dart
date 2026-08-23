import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_selector_page.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/features/company/company_shell.dart';

/// Dev-only Company admin bearer entry (L04).
class DevCompanySessionPage extends StatefulWidget {
  const DevCompanySessionPage({super.key});

  @override
  State<DevCompanySessionPage> createState() => _DevCompanySessionPageState();
}

class _DevCompanySessionPageState extends State<DevCompanySessionPage> {
  final _baseCtrl = TextEditingController(text: companyContext.baseUrl);
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
    companyContext.setSession(
      baseUrl: _baseCtrl.text.trim(),
      bearerToken: _tokenCtrl.text.trim(),
    );
    try {
      final me = await companyContext.api.me();
      final contours = me['contours'];
      if (contours is! List || !contours.contains('company')) {
        companyContext.clear();
        setState(() => _error = 'Token must have company contour (company.admin membership)');
        return;
      }
      final employee = me['employee'] as Map<String, dynamic>?;
      final memberships = employee?['memberships'];
      if (memberships is! List) {
        companyContext.clear();
        setState(() => _error = 'No employee memberships');
        return;
      }
      final adminMemberships = memberships
          .whereType<Map<String, dynamic>>()
          .where((m) => m['role'] == 'company.admin')
          .toList();
      if (adminMemberships.isEmpty) {
        companyContext.clear();
        setState(() => _error = 'company.admin membership required');
        return;
      }
      if (!mounted) return;
      if (adminMemberships.length == 1) {
        final id = adminMemberships.first['company_id'] as String;
        companyContext.selectCompany(id: id, name: id);
        _openShell();
        return;
      }
      final picked = await Navigator.of(context).push<Set<String>>(
        MaterialPageRoute(
          builder: (_) => AppSelectorPage(
            title: 'Select company',
            items: [
              for (final m in adminMemberships)
                AppSelectorItem(
                  id: m['company_id'] as String,
                  title: m['company_id'] as String,
                  subtitle: m['role'] as String?,
                ),
            ],
            showRadios: true,
            popOnSelect: true,
          ),
        ),
      );
      if (picked == null || picked.isEmpty) {
        companyContext.clear();
        return;
      }
      final id = picked.first;
      companyContext.selectCompany(id: id, name: id);
      if (!mounted) return;
      _openShell();
    } catch (e) {
      companyContext.clear();
      setState(() => _error = e.toString());
    }
  }

  void _openShell() {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const CompanyShell()),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Company session'),
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AppTextField(controller: _baseCtrl, label: 'API base URL'),
            const SizedBox(height: AppSpacing.md),
            AppTextField(
              controller: _tokenCtrl,
              label: 'Bearer token (company.admin JWT)',
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
