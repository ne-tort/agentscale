import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Full-page employee invite — no password field (L04 ux-contract).
class CompanyInviteEmployeePage extends StatefulWidget {
  const CompanyInviteEmployeePage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyInviteEmployeePage> createState() => _CompanyInviteEmployeePageState();
}

class _CompanyInviteEmployeePageState extends State<CompanyInviteEmployeePage> {
  final _formKey = GlobalKey<FormState>();
  final _emailCtrl = TextEditingController();
  final _nameCtrl = TextEditingController();
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _emailCtrl.dispose();
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _invite() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await companyContext.api.inviteEmployee(
        companyId: widget.companyId,
        email: _emailCtrl.text.trim(),
        displayName: _nameCtrl.text.trim(),
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Invite employee'),
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_error != null) InlineErrorBanner(message: _error!),
            const Text('Invite via Keycloak — password is not accepted here.'),
            const SizedBox(height: AppSpacing.md),
            AppForm(
              formKey: _formKey,
              children: [
                AppTextField(
                  controller: _emailCtrl,
                  label: 'Email',
                  keyboardType: TextInputType.emailAddress,
                  validator: (v) {
                    final email = v?.trim() ?? '';
                    if (email.isEmpty || !email.contains('@')) return 'Valid email required';
                    return null;
                  },
                ),
                AppTextField(
                  controller: _nameCtrl,
                  label: 'Display name (optional)',
                ),
                AppButton(
                  label: _saving ? 'Inviting…' : 'Invite',
                  onPressed: _saving ? null : _invite,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
