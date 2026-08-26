import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Agent policy fields for a company (budgets / idle / attachment).
///
/// Tool preset, preferred provider, and ingress HMAC are not edited here —
/// backend keeps defaults / existing values on save.
class AdminCompanyAgentPolicyPage extends StatelessWidget {
  const AdminCompanyAgentPolicyPage({super.key});

  static const _idleChoices = AdminCompanyDetailController.idlePauseChoices;
  static const _attachmentChoices = AdminCompanyDetailController.attachmentMbChoices;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ctrl = AdminCompanyDetailScope.of(context);
    return ListenableBuilder(
      listenable: ctrl,
      builder: (context, _) {
        final idleValue = _idleChoices.contains(ctrl.idlePauseHours)
            ? ctrl.idlePauseHours
            : (ctrl.idlePauseHours.trim().isEmpty ? '' : ctrl.idlePauseHours);
        final attachmentValue = ctrl.maxAttachmentMb;
        final attachmentChoices = {
          ..._attachmentChoices,
          if (!_attachmentChoices.contains(attachmentValue)) attachmentValue,
        }.toList()
          ..sort();

        return AppScaffold(
          title: Text(l10n.adminPolicy),
          body: ListView(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            children: [
              AppChoicePreference<String>(
                title: l10n.adminIdlePauseAfterHours,
                icon: Icons.pause_circle_outline_rounded,
                value: idleValue,
                choices: _idleChoices,
                keyFor: (v) => v.isEmpty ? '__off__' : v,
                labelFor: (v) => v.isEmpty
                    ? l10n.adminIdlePauseNever
                    : l10n.adminIdlePauseHours(v),
                presentValue: (v) => v.isEmpty
                    ? l10n.adminIdlePauseNever
                    : l10n.adminIdlePauseHours(v),
                onSave: (v) async {
                  await ctrl.savePolicy(idlePauseHours: v);
                },
              ),
              AppChoicePreference<int>(
                title: l10n.adminMaxChatAttachmentMb,
                icon: Icons.attachment_rounded,
                value: attachmentValue,
                choices: attachmentChoices,
                keyFor: (v) => '$v',
                labelFor: (v) => '$v ${l10n.commonMbUnit}',
                presentValue: (v) => '$v ${l10n.commonMbUnit}',
                onSave: (v) async {
                  await ctrl.savePolicy(maxAttachmentMb: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxAgentTokensMonth,
                icon: Icons.numbers_rounded,
                value: ctrl.maxTokensMonth,
                digitsOnly: true,
                presentValue: (v) =>
                    v.trim().isEmpty ? l10n.commonOff : v,
                onSave: (v) async {
                  await ctrl.savePolicy(maxTokensMonth: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxTokensPerRun,
                icon: Icons.speed_rounded,
                value: ctrl.maxTokensPerRun,
                digitsOnly: true,
                presentValue: (v) =>
                    v.trim().isEmpty ? l10n.commonOff : v,
                onSave: (v) async {
                  await ctrl.savePolicy(maxTokensPerRun: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxUsdCostMonth,
                icon: Icons.attach_money_rounded,
                value: ctrl.maxCostUsdMonth,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                presentValue: (v) =>
                    v.trim().isEmpty ? l10n.commonOff : v,
                onSave: (v) async {
                  await ctrl.savePolicy(maxCostUsdMonth: v);
                },
              ),
            ],
          ),
        );
      },
    );
  }
}
