import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
  Object? _error;

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
        _error = e;
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.companyInviteEmployee),
      body: Padding(
        padding: EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
            Text(l10n.companyInviteViaKeycloakPasswordNotAccepted),
            const SizedBox(height: AppSpacing.md),
            Form(
              key: _formKey,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AppTextField(
                    controller: _emailCtrl,
                    label: l10n.commonEmail,
                    keyboardType: TextInputType.emailAddress,
                    validator: (v) {
                      final email = v?.trim() ?? '';
                      if (email.isEmpty || !email.contains('@')) {
                        return l10n.companyValidEmailRequired;
                      }
                      return null;
                    },
                  ),
                  SizedBox(height: AppSpacing.md),
                  AppTextField(
                    controller: _nameCtrl,
                    label: l10n.commonDisplayNameOptional,
                  ),
                  SizedBox(height: AppSpacing.md),
                  AppAsyncButton(
                    label: _saving ? l10n.companyInviting : l10n.commonInvite,
                    busy: _saving,
                    onPressed: _saving ? null : _invite,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
