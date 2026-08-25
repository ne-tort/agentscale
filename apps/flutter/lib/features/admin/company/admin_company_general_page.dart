import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company identity + subscription fields (no section headers).
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
          title: Text(l10n.commonCompany),
          body: ListView(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            children: [
              AppPreferenceTile(
                title: l10n.adminCompanyName,
                icon: Icons.badge_outlined,
                subtitle: Text(ctrl.companyName),
              ),
              AppValuePreference<String>(
                title: l10n.commonDescription,
                icon: Icons.notes_rounded,
                value: ctrl.description,
                maxLines: 3,
                onSave: ctrl.saveDescription,
              ),
              AppSubscriptionPreference(
                lifetime: ctrl.subscriptionLifetime,
                endsAt: ctrl.subscriptionEnds,
                onLifetimeChanged: (v) async {
                  final err = await ctrl.saveSubscription(lifetime: v);
                  if (err != null && context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(content: Text(l10n.adminSetEndDateOrLifetime)),
                    );
                  }
                },
                onEndsAtSave: (v) async {
                  final err = await ctrl.saveSubscription(endsAt: v);
                  if (err != null && context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(content: Text(l10n.adminSetEndDateOrLifetime)),
                    );
                  }
                },
              ),
            ],
          ),
        );
      },
    );
  }
}
