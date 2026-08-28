import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/company_metrics_wrap.dart';
import 'package:prodavan/features/admin/company/admin_company_agent_policy_page.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_controller.dart';
import 'package:prodavan/features/admin/company/admin_company_events_page.dart';
import 'package:prodavan/features/admin/company/admin_company_general_page.dart';
import 'package:prodavan/features/admin/company/admin_company_quotas_page.dart';
import 'package:prodavan/features/admin/widgets/admin_metrics_alerts.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin company hub — metrics + laconic nav to preference sub-pages.
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
  late final AppAutoRefreshBinder _autoRefresh;

  @override
  void initState() {
    super.initState();
    _controller = AdminCompanyDetailController(
      companyId: widget.companyId,
      companyName: widget.companyName,
    );
    _controller.addListener(_onControllerUpdate);
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _controller.load(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
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
    _autoRefresh.dispose();
    _controller.removeListener(_onControllerUpdate);
    _controller.dispose();
    super.dispose();
  }

  Future<void> _copyCompanyId() async {
    await Clipboard.setData(ClipboardData(text: _controller.companyId));
    if (!mounted) return;
    AppSnackBar.info(context, AppLocalizations.of(context).companyIdCopied);
  }

  String _generalSubtitle(AppLocalizations l10n, AdminCompanyDetailController ctrl) {
    final desc = ctrl.description.trim();
    if (desc.isNotEmpty) return desc;
    final phone = ctrl.phone.trim();
    final email = ctrl.contactEmail.trim();
    if (phone.isNotEmpty && email.isNotEmpty) return '$phone · $email';
    if (phone.isNotEmpty) return phone;
    if (email.isNotEmpty) return email;
    return ctrl.companyName;
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
            title: Text(ctrl.companyName),
            body: ctrl.loading
                ? const Center(child: CircularProgressIndicator())
                : ListView(
                    padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
                    children: [
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            AdminMetricsAlerts(metrics: ctrl.metrics),
                            const SizedBox(height: AppSpacing.sm),
                            CompanyMetricsWrap(
                              metrics: ctrl.metrics,
                              includeAgentDetail: true,
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: AppSpacing.sm),
                      AppValuePreference<String>(
                        title: l10n.companyLoginId,
                        icon: Icons.badge_outlined,
                        value: ctrl.companyId,
                        enabled: false,
                        presentValue: (v) => v,
                        onSave: (_) async {},
                        onTap: _copyCompanyId,
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
                          await ctrl.savePassword(v.trim());
                          if (!context.mounted) return;
                          AppSnackBar.success(context, l10n.companyPasswordChanged);
                        },
                      ),
                      AppNavPreference(
                        title: l10n.adminCompanyGeneral,
                        icon: Icons.business_outlined,
                        subtitle: Text(_generalSubtitle(l10n, ctrl)),
                        onTap: () => pushCompanySubPage(
                          context,
                          const AdminCompanyGeneralPage(),
                        ),
                      ),
                      AppNavPreference(
                        title: l10n.adminQuotas,
                        icon: Icons.inventory_2_outlined,
                        subtitle: Text(
                          l10n.adminQuotasSummary(
                            '${ctrl.maxCabinets}',
                            '${ctrl.maxPackages}',
                            '${ctrl.maxBundleMb}',
                          ),
                        ),
                        onTap: () => pushCompanySubPage(
                          context,
                          const AdminCompanyQuotasPage(),
                        ),
                      ),
                      AppNavPreference(
                        title: l10n.adminPolicy,
                        icon: Icons.smart_toy_outlined,
                        subtitle: Text(l10n.adminAgentLimits),
                        onTap: () => pushCompanySubPage(
                          context,
                          const AdminCompanyAgentPolicyPage(),
                        ),
                      ),
                      AppNavPreference(
                        title: l10n.adminEvents,
                        icon: Icons.event_note_outlined,
                        subtitle: Text('${ctrl.platformEvents.length}'),
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
