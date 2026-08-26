import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company identity + subscription — seamless preference saves (AI-key style).
class AdminCompanyGeneralPage extends StatelessWidget {
  const AdminCompanyGeneralPage({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ctrl = AdminCompanyDetailScope.of(context);
    return ListenableBuilder(
      listenable: ctrl,
      builder: (context, _) {
        return AppScaffold(
          title: Text(l10n.adminCompanyGeneral),
          body: ListView(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            children: [
              AppValuePreference<String>(
                title: l10n.commonName,
                icon: Icons.label_outline_rounded,
                value: ctrl.companyName,
                validateInput: (v) => v.trim().isNotEmpty,
                onSave: ctrl.saveName,
              ),
              AppValuePreference<String>(
                title: l10n.commonDescription,
                icon: Icons.notes_rounded,
                value: ctrl.description,
                presentValue: (v) =>
                    v.trim().isEmpty ? l10n.commonNotSet : v,
                onSave: ctrl.saveDescription,
              ),
              AppValuePreference<String>(
                title: l10n.commonEmail,
                icon: Icons.email_outlined,
                value: ctrl.contactEmail,
                hintText: l10n.commonEmail,
                keyboardType: TextInputType.emailAddress,
                presentValue: (v) =>
                    v.trim().isEmpty ? l10n.commonNotSet : v,
                validateInput: (v) {
                  final email = v.trim();
                  return email.isEmpty || email.contains('@');
                },
                onSave: ctrl.saveContactEmail,
              ),
              AppValuePreference<String>(
                title: l10n.commonPhone,
                icon: Icons.phone_outlined,
                value: ctrl.phone,
                keyboardType: TextInputType.phone,
                presentValue: (v) =>
                    v.trim().isEmpty ? l10n.commonNotSet : v,
                onSave: ctrl.savePhone,
              ),
              AppSubscriptionPreference(
                endsAt: ctrl.subscriptionEnds,
                onEndsAtSave: (v) => ctrl.saveSubscription(endsAt: v),
              ),
            ],
          ),
        );
      },
    );
  }
}
