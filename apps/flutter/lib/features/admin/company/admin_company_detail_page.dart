import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/company_metrics_wrap.dart';
import 'package:prodavan/features/admin/company/admin_company_agent_policy_page.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/features/admin/company/admin_company_events_page.dart';
import 'package:prodavan/features/admin/company/admin_company_general_page.dart';
import 'package:prodavan/features/admin/company/admin_company_quotas_page.dart';
import 'package:prodavan/features/admin/widgets/admin_metrics_alerts.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin company hub — metrics + navigation to sub-pages (L04).
class AdminCompanyDetailPage extends StatefulWidget {
  const AdminCompanyDetailPage({
    super.key,
    required this.companyId,
    required this.companyName,
  });

  final String companyId;
  final String companyName;

  @override
  State<AdminCompanyDetailPage> createState() => _AdminCompanyDetailPageState();
}

class _AdminCompanyDetailPageState extends State<AdminCompanyDetailPage> {
  late final AdminCompanyDetailController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AdminCompanyDetailController(
      companyId: widget.companyId,
      companyName: widget.companyName,
    );
    _controller.addListener(_onControllerUpdate);
    _controller.load();
  }

  void _onControllerUpdate() {
    final err = _controller.error;
    if (err != null && mounted) {
      AppErrors.showSnack(context, err);
      _controller.error = null;
    }
  }

  @override
  void dispose() {
    _controller.removeListener(_onControllerUpdate);
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AdminCompanyDetailScope(
      controller: _controller,
      child: ListenableBuilder(
        listenable: _controller,
        builder: (context, _) {
          final ctrl = _controller;
          return AppScaffold(
            title: Text(widget.companyName),
            actions: [
              IconButton(
                onPressed: ctrl.loading ? null : ctrl.load,
                icon: const Icon(Icons.refresh),
              ),
            ],
            body: ctrl.loading
                ? const Center(child: CircularProgressIndicator())
                : ListView(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    children: [
                      AdminMetricsAlerts(metrics: ctrl.metrics),
                      const SizedBox(height: AppSpacing.sm),
                      CompanyMetricsWrap(metrics: ctrl.metrics, includeAgentDetail: true),
                      const SizedBox(height: AppSpacing.md),
                      AppNavPreference(
                        title: l10n.commonCompany,
                        icon: Icons.business_outlined,
                        onTap: () => pushCompanySubPage(
                          context,
                          const AdminCompanyGeneralPage(),
                        ),
                      ),
                      AppNavPreference(
                        title: l10n.adminCabinetQuotas,
                        icon: Icons.inventory_2_outlined,
                        onTap: () => pushCompanySubPage(
                          context,
                          const AdminCompanyQuotasPage(),
                        ),
                      ),
                      AppNavPreference(
                        title: l10n.adminAgentRuntimePolicy,
                        icon: Icons.smart_toy_outlined,
                        onTap: () => pushCompanySubPage(
                          context,
                          const AdminCompanyAgentPolicyPage(),
                        ),
                      ),
                      AppNavPreference(
                        title: l10n.adminPlatformEvents,
                        icon: Icons.event_note_outlined,
                        onTap: () => pushCompanySubPage(
                          context,
                          const AdminCompanyEventsPage(),
                        ),
                      ),
                    ],
                  ),
          );
        },
      ),
    );
  }
}
