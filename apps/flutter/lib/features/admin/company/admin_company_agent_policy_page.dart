import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Agent policy fields for a company.
class AdminCompanyAgentPolicyPage extends StatelessWidget {
  const AdminCompanyAgentPolicyPage({super.key});

  static const _providers = AdminCompanyDetailController.providerChoices;
  static const _idleChoices = AdminCompanyDetailController.idlePauseChoices;
  static const _attachmentChoices = AdminCompanyDetailController.attachmentMbChoices;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ctrl = AdminCompanyDetailScope.of(context);
    return ListenableBuilder(
      listenable: ctrl,
      builder: (context, _) {
        final providerValue = _providers.contains(ctrl.preferredProvider)
            ? ctrl.preferredProvider
            : '';
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
              AppChoicePreference<String>(
                title: l10n.adminPreferredProvider,
                icon: Icons.cloud_outlined,
                value: providerValue,
                choices: _providers,
                keyFor: (v) => v.isEmpty ? '__none__' : v,
                labelFor: (v) => v.isEmpty ? l10n.commonNotSet : v,
                presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
                onSave: (v) async {
                  await ctrl.savePolicy(preferredProvider: v);
                },
              ),
              AppChoicePreference<String>(
                title: l10n.adminIdlePauseAfterHours,
                icon: Icons.pause_circle_outline_rounded,
                value: idleValue,
                choices: _idleChoices,
                keyFor: (v) => v.isEmpty ? '__off__' : v,
                labelFor: (v) => v.isEmpty ? l10n.commonOff : v,
                presentValue: (v) => v.isEmpty ? l10n.commonOff : '$v ч',
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
              AppSwitchPreference(
                title: l10n.adminPlatformFallback,
                icon: Icons.swap_horiz_rounded,
                value: ctrl.platformFallback,
                onChanged: (v) async {
                  await ctrl.savePolicy(platformFallback: v);
                },
              ),
              AppValuePreference<String>(
                title: l10n.adminModelAllowlist,
                icon: Icons.list_alt_rounded,
                value: ctrl.modelAllowlist,
                presentValue: (v) =>
                    v.trim().isEmpty ? l10n.commonNotSet : v,
                onSave: (v) async {
                  await ctrl.savePolicy(modelAllowlist: v);
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
              AppValuePreference<String>(
                title: l10n.adminWebhookHmacSecret,
                icon: Icons.webhook_rounded,
                value: '',
                obscureText: true,
                presentValue: (_) => ctrl.webhookHmacConfigured
                    ? '••••••••'
                    : l10n.commonNotSet,
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
                    ? '••••••••'
                    : l10n.commonNotSet,
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
