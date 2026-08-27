import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company org login + password (admin-editable login).
class AdminCompanyAccessPage extends StatelessWidget {
  const AdminCompanyAccessPage({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ctrl = AdminCompanyDetailScope.of(context);
    return ListenableBuilder(
      listenable: ctrl,
      builder: (context, _) {
        return AppScaffold(
          title: Text(l10n.settings),
          body: ListView(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            children: [
              AppValuePreference<String>(
                title: l10n.companyLoginId,
                icon: Icons.badge_outlined,
                value: ctrl.companyId,
                enabled: false,
                presentValue: (v) => v,
                onSave: (_) async {},
                onTap: () async {
                  await Clipboard.setData(ClipboardData(text: ctrl.companyId));
                  if (!context.mounted) return;
                  AppSnackBar.info(context, l10n.companyIdCopied);
                },
              ),
              AppValuePreference<String>(
                title: l10n.companyLogin,
                icon: Icons.login_outlined,
                value: ctrl.loginUsername,
                hintText: l10n.companyLoginId,
                validateInput: (v) => v.trim().length >= 3,
                onSave: (v) async {
                  await ctrl.saveLogin(v.trim());
                },
              ),
              AppValuePreference<String>(
                title: l10n.companyPassword,
                icon: Icons.key_outlined,
                value: '',
                obscureText: true,
                hintText: l10n.companyPasswordHint,
                invalidMessage: l10n.companyPasswordHint,
                presentValue: (_) =>
                    ctrl.passwordSet ? '••••••••' : l10n.commonNotSet,
                formatInputValue: (_) => '',
                validateInput: (raw) => raw.trim().length >= 8,
                onSave: (v) async {
                  final trimmed = v.trim();
                  if (trimmed.length < 8) return;
                  await ctrl.savePassword(trimmed);
                  if (!context.mounted) return;
                  AppSnackBar.success(context, l10n.companyPasswordChanged);
                },
              ),
            ],
          ),
        );
      },
    );
  }
}
