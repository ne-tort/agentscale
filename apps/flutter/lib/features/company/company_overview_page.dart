import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/company_metrics_wrap.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company overview — org metrics + Keycloak login credentials (L04).
class CompanyOverviewPage extends StatefulWidget {
  const CompanyOverviewPage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyOverviewPage> createState() => _CompanyOverviewPageState();
}

class _CompanyOverviewPageState extends State<CompanyOverviewPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Object? _error;
  Map<String, dynamic>? _metrics;
  bool _passwordSet = false;

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _reload(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _reload();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final summary = await companyContext.api.getSummary(widget.companyId);
      if (!mounted) return;
      final metrics = summary['metrics'] as Map<String, dynamic>?;
      final passwordSet = summary['password_set'] == true;
      if (silent &&
          appRefreshDataEquals(_metrics, metrics) &&
          _passwordSet == passwordSet &&
          !_loading) {
        return;
      }
      setState(() {
        _metrics = metrics;
        _passwordSet = passwordSet;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _savePassword(String raw) async {
    final trimmed = raw.trim();
    if (trimmed.length < 8) return;
    await companyContext.api.setCompanyPassword(
      companyId: widget.companyId,
      password: trimmed,
    );
    if (!mounted) return;
    setState(() => _passwordSet = true);
    AppSnackBar.success(
      context,
      AppLocalizations.of(context).companyPasswordChanged,
    );
  }

  Future<void> _copyCompanyId() async {
    await Clipboard.setData(ClipboardData(text: widget.companyId));
    if (!mounted) return;
    AppSnackBar.info(context, AppLocalizations.of(context).companyIdCopied);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: [
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                    child: AppStatusBanner(
                      severity: AppStatusSeverity.error,
                      message: AppErrors.localize(context, _error!),
                    ),
                  ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                  child: CompanyMetricsWrap(metrics: _metrics),
                ),
                const SizedBox(height: AppSpacing.sm),
                AppValuePreference<String>(
                  title: l10n.companyLoginId,
                  icon: Icons.badge_outlined,
                  value: widget.companyId,
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
                      _passwordSet ? '••••••••' : l10n.commonNotSet,
                  formatInputValue: (_) => '',
                  validateInput: (raw) => raw.trim().length >= 8,
                  onSave: _savePassword,
                ),
              ],
            ),
    );
  }
}
