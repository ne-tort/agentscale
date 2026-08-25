import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Cabinet quota limits for a company.
class AdminCompanyQuotasPage extends StatelessWidget {
  const AdminCompanyQuotasPage({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ctrl = AdminCompanyDetailScope.of(context);
    return ListenableBuilder(
      listenable: ctrl,
      builder: (context, _) {
        return AppScaffold(
          title: Text(l10n.adminCabinetQuotas),
          body: ListView(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            children: [
              AppValuePreference<String>(
                title: l10n.adminMaxCabinets,
                icon: Icons.view_module_outlined,
                value: '${ctrl.maxCabinets}',
                digitsOnly: true,
                validateInput: (v) => ctrl.validatePositiveInt(v, min: 1),
                inputToValue: (v) => v,
                onSave: (v) async {
                  await ctrl.saveQuotas(maxCabinets: int.parse(v.trim()));
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxPackagesPerCabinet,
                icon: Icons.inventory_2_outlined,
                value: '${ctrl.maxPackages}',
                digitsOnly: true,
                validateInput: (v) => ctrl.validatePositiveInt(v, min: 0),
                inputToValue: (v) => v,
                onSave: (v) async {
                  await ctrl.saveQuotas(maxPackages: int.parse(v.trim()));
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxBundleImportMb,
                icon: Icons.upload_file_outlined,
                value: '${ctrl.maxBundleMb}',
                digitsOnly: true,
                validateInput: (v) => ctrl.validatePositiveInt(v, min: 1),
                inputToValue: (v) => v,
                onSave: (v) async {
                  await ctrl.saveQuotas(maxBundleMb: int.parse(v.trim()));
                },
              ),
            ],
          ),
        );
      },
    );
  }
}
