import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page employee invite — no password field (L04 ux-contract).
class CompanyInviteEmployeePage extends StatefulWidget {
  const CompanyInviteEmployeePage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyInviteEmployeePage> createState() => _CompanyInviteEmployeePageState();
}

class _CompanyInviteEmployeePageState extends State<CompanyInviteEmployeePage> {
  String _email = '';
  String _displayName = '';
  bool _saving = false;
  Object? _error;

  Future<void> _invite() async {
    final email = _email.trim();
    if (email.isEmpty || !email.contains('@')) {
      setState(() => _error = AppLocalizations.of(context).companyValidEmailRequired);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await companyContext.api.inviteEmployee(
        companyId: widget.companyId,
        email: email,
        displayName: _displayName.trim(),
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
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          if (_error != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              child: AppStatusBanner(
                severity: AppStatusSeverity.error,
                message: AppErrors.localize(context, _error!),
              ),
            ),
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.sm,
            ),
            child: Text(l10n.companyInviteViaKeycloakPasswordNotAccepted),
          ),
          AppValuePreference<String>(
            title: l10n.commonEmail,
            icon: Icons.email_outlined,
            value: _email,
            enabled: !_saving,
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: (v) async => setState(() => _email = v.trim()),
          ),
          AppValuePreference<String>(
            title: l10n.commonDisplayNameOptional,
            icon: Icons.badge_outlined,
            value: _displayName,
            enabled: !_saving,
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: (v) async => setState(() => _displayName = v.trim()),
          ),
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: AppAsyncButton(
              label: _saving ? l10n.companyInviting : l10n.commonInvite,
              busy: _saving,
              onPressed: _saving ? null : _invite,
            ),
          ),
        ],
      ),
    );
  }
}
