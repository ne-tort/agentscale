import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Agent runtime policy fields for a company.
class AdminCompanyAgentPolicyPage extends StatelessWidget {
  const AdminCompanyAgentPolicyPage({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ctrl = AdminCompanyDetailScope.of(context);
    return ListenableBuilder(
      listenable: ctrl,
      builder: (context, _) {
        return AppScaffold(
          title: Text(l10n.adminAgentRuntimePolicy),
          body: ListView(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            children: [
              AppChoicePreference<String>(
                title: l10n.adminToolPreset,
                icon: Icons.tune_rounded,
                value: AdminCompanyDetailController.toolPresets.contains(ctrl.toolPreset)
                    ? ctrl.toolPreset
                    : 'workspace_dev',
                choices: AdminCompanyDetailController.toolPresets,
                keyFor: (v) => v,
                labelFor: (v) => v,
                iconFor: (v) => switch (v) {
                  'chat_readonly' => Icons.chat_bubble_outline_rounded,
                  'workspace_full' => Icons.code_rounded,
                  _ => Icons.developer_mode_outlined,
                },
                onSave: (v) async {
                  await ctrl.savePolicy(toolPreset: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminPreferredProviderOptional,
                icon: Icons.cloud_outlined,
                value: ctrl.preferredProvider,
                onSave: (v) async {
                  await ctrl.savePolicy(preferredProvider: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminModelAllowlist,
                icon: Icons.list_alt_rounded,
                value: ctrl.modelAllowlist,
                onSave: (v) async {
                  await ctrl.savePolicy(modelAllowlist: v);
                },
              ),
              AppSwitchPreference(
                title: l10n.adminPlatformFallback,
                icon: Icons.swap_horiz_rounded,
                value: ctrl.platformFallback,
                onChanged: (v) async {
                  await ctrl.savePolicy(platformFallback: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxAgentTokensMonth,
                icon: Icons.numbers_rounded,
                value: ctrl.maxTokensMonth,
                digitsOnly: true,
                onSave: (v) async {
                  await ctrl.savePolicy(maxTokensMonth: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxTokensPerRun,
                icon: Icons.speed_rounded,
                value: ctrl.maxTokensPerRun,
                digitsOnly: true,
                onSave: (v) async {
                  await ctrl.savePolicy(maxTokensPerRun: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxUsdCostMonth,
                icon: Icons.attach_money_rounded,
                value: ctrl.maxCostUsdMonth,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                onSave: (v) async {
                  await ctrl.savePolicy(maxCostUsdMonth: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminMaxChatAttachmentMb,
                icon: Icons.attachment_rounded,
                value: '${ctrl.maxAttachmentMb}',
                digitsOnly: true,
                validateInput: (v) => ctrl.validatePositiveInt(v, min: 1),
                onSave: (v) async {
                  await ctrl.savePolicy(maxAttachmentMb: int.parse(v.trim()));
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminIdlePauseAfterHours,
                icon: Icons.pause_circle_outline_rounded,
                value: ctrl.idlePauseHours,
                digitsOnly: true,
                onSave: (v) async {
                  await ctrl.savePolicy(idlePauseHours: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminWebhookHmacSecret,
                icon: Icons.webhook_rounded,
                value: '',
                obscureText: true,
                presentValue: (_) => ctrl.webhookHmacConfigured
                    ? l10n.adminWebhookHmacConfigured
                    : l10n.adminWebhookHmacNotSet,
                formatInputValue: (_) => '',
                onSave: (v) async {
                  if (v.trim().isEmpty) return;
                  await ctrl.savePolicy(webhookSecret: v.trim());
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminTelegramHmacSecret,
                icon: Icons.telegram,
                value: '',
                obscureText: true,
                presentValue: (_) => ctrl.telegramHmacConfigured
                    ? l10n.adminTelegramHmacConfigured
                    : l10n.adminTelegramHmacNotSet,
                formatInputValue: (_) => '',
                onSave: (v) async {
                  if (v.trim().isEmpty) return;
                  await ctrl.savePolicy(telegramSecret: v.trim());
                },
              ),
            ],
          ),
        );
      },
    );
  }
}
