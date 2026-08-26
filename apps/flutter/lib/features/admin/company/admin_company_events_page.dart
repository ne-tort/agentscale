import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform events + maintenance actions for a company.
class AdminCompanyEventsPage extends StatelessWidget {
  const AdminCompanyEventsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ctrl = AdminCompanyDetailScope.of(context);
    return ListenableBuilder(
      listenable: ctrl,
      builder: (context, _) {
        return AppScaffold(
          title: Text(l10n.adminEvents),
          body: ListView(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            children: [
              AppPreferenceTile(
                title: l10n.adminDrainProjectTriggers,
                icon: Icons.pause_circle_outline_rounded,
                enabled: !ctrl.busy,
                onTap: ctrl.busy
                    ? null
                    : () async {
                        try {
                          final result = await ctrl.drainTriggers();
                          if (!context.mounted) return;
                          AppSnackBar.success(
                            context,
                            l10n.adminDrainedTriggers('${result['count'] ?? 0}'),
                          );
                        } catch (_) {}
                      },
              ),
              AppPreferenceTile(
                title: l10n.adminSweepIdlePause,
                icon: Icons.cleaning_services_outlined,
                enabled: !ctrl.busy,
                onTap: ctrl.busy
                    ? null
                    : () async {
                        try {
                          final result = await ctrl.sweepIdlePause(
                            targetCompanyId: ctrl.companyId,
                          );
                          if (!context.mounted) return;
                          AppSnackBar.success(
                            context,
                            l10n.adminIdlePausedProjectsInCompany(
                              '${result['count'] ?? 0}',
                            ),
                          );
                        } catch (_) {}
                      },
              ),
              AppPreferenceTile(
                title: l10n.adminSweepIdlePauseAll,
                icon: Icons.layers_clear_outlined,
                enabled: !ctrl.busy,
                onTap: ctrl.busy
                    ? null
                    : () async {
                        try {
                          final result = await ctrl.sweepIdlePause();
                          if (!context.mounted) return;
                          final count = result['count'] ?? 0;
                          final companies = (result['companies'] is List)
                              ? (result['companies'] as List).length
                              : 0;
                          AppSnackBar.success(
                            context,
                            l10n.adminPlatformIdleSweep('$count', '$companies'),
                          );
                        } catch (_) {}
                      },
              ),
              const SizedBox(height: AppSpacing.md),
              if (ctrl.platformEvents.isEmpty)
                EmptyPlaceholder(
                  title: l10n.adminNoPlatformEventsYet,
                  icon: Icons.event_note_outlined,
                  fillViewport: false,
                )
              else
                for (final ev in ctrl.platformEvents.take(20))
                  AppPreferenceTile(
                    title: ev['event_type']?.toString() ?? 'event',
                    icon: Icons.circle_outlined,
                    subtitle: Text(
                      [
                        if (ev['created_at'] != null) ev['created_at'].toString(),
                        if (ev['actor_sub'] != null) ev['actor_sub'].toString(),
                      ].join(' · '),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
            ],
          ),
        );
      },
    );
  }
}
