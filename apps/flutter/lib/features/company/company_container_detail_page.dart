import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company container detail — pause / resume / delete scoped to org.
class CompanyContainerDetailPage extends StatefulWidget {
  const CompanyContainerDetailPage({
    super.key,
    required this.companyId,
    required this.projectId,
    required this.projectName,
  });

  final String companyId;
  final String projectId;
  final String projectName;

  @override
  State<CompanyContainerDetailPage> createState() => _CompanyContainerDetailPageState();
}

class _CompanyContainerDetailPageState extends State<CompanyContainerDetailPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  bool _busy = false;
  Map<String, dynamic>? _item;
  String _title = '';

  @override
  void initState() {
    super.initState();
    _title = widget.projectName;
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _load(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _load();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent && mounted) setState(() => _loading = true);
    try {
      final item = await companyContext.api.getContainer(
        companyId: widget.companyId,
        projectId: widget.projectId,
      );
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_item, item) && !_loading) return;
      setState(() {
        _item = item;
        _title = item['project_name'] as String? ?? widget.projectName;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  String _cell(dynamic v, AppLocalizations l10n) {
    if (v == null) return l10n.commonEmDash;
    final s = '$v'.trim();
    return s.isEmpty ? l10n.commonEmDash : s;
  }

  String _owner(AppLocalizations l10n) {
    final name = _item?['owner_display_name'] as String?;
    final email = _item?['owner_email'] as String?;
    if (name != null && name.trim().isNotEmpty) {
      if (email != null && email.trim().isNotEmpty) return '$name · $email';
      return name;
    }
    return _cell(email, l10n);
  }

  Future<void> _pause() async {
    setState(() => _busy = true);
    try {
      await companyContext.api.pauseContainer(
        companyId: widget.companyId,
        projectId: widget.projectId,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resume() async {
    setState(() => _busy = true);
    try {
      await companyContext.api.resumeContainer(
        companyId: widget.companyId,
        projectId: widget.projectId,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete() async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: l10n.adminDeleteContainerConfirm(_title),
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    setState(() => _busy = true);
    try {
      await companyContext.api.deleteContainer(
        companyId: widget.companyId,
        projectId: widget.projectId,
      );
      if (!mounted) return;
      Navigator.of(context).pop();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final status = _item?['status'] as String?;
    final paused = status == 'paused';
    final colors = context.appColors;

    return AppScaffold(
      title: Text(_title),
      body: _loading && _item == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: [
                if (paused)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                    child: AppStatusBanner(
                      severity: AppStatusSeverity.warning,
                      message: l10n.projectPausedBanner,
                    ),
                  ),
                if (containerRuntimeNeedsAttention(_item))
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                    child: AppStatusBanner(
                      severity: AppStatusSeverity.info,
                      message: l10n.adminContainerRuntimeAttention,
                    ),
                  ),
                AppPreferenceTile(
                  title: l10n.adminContainerColStatus,
                  icon: Icons.circle,
                  subtitle: Text(
                    status == 'active'
                        ? l10n.adminContainerStatusActive
                        : status == 'paused'
                            ? l10n.adminContainerStatusPaused
                            : _cell(status, l10n),
                    style: TextStyle(color: paused ? colors.warning : null),
                  ),
                ),
                AppPreferenceTile(
                  title: l10n.adminContainerRuntimeRef,
                  icon: Icons.dns_outlined,
                  subtitle: Text(_cell(_item?['container_ref'], l10n)),
                ),
                AppPreferenceTile(
                  title: l10n.adminContainerColEmployee,
                  icon: Icons.person_outline,
                  subtitle: Text(_owner(l10n)),
                ),
                AppPreferenceTile(
                  title: l10n.adminContainerColCabinet,
                  icon: Icons.folder_outlined,
                  subtitle: Text(_cell(_item?['cabinet_name'], l10n)),
                ),
                AppPreferenceTile(
                  title: l10n.adminContainerColProvider,
                  icon: Icons.smart_toy_outlined,
                  subtitle: Text(_cell(_item?['agent_provider'], l10n)),
                ),
                AppPreferenceTile(
                  title: l10n.adminContainerMetricsHole,
                  icon: Icons.monitor_heart_outlined,
                  subtitle: Text(formatContainerRuntimeDetail(_item, l10n)),
                ),
                const SizedBox(height: AppSpacing.md),
                if (!paused)
                  AppPreferenceTile(
                    title: l10n.projectPauseProject,
                    icon: Icons.pause_circle_outline,
                    enabled: !_busy,
                    onTap: _busy ? null : _pause,
                  ),
                if (paused)
                  AppPreferenceTile(
                    title: l10n.projectResumeProject,
                    icon: Icons.play_circle_outline,
                    enabled: !_busy,
                    onTap: _busy ? null : _resume,
                  ),
                AppPreferenceTile(
                  title: l10n.commonDelete,
                  icon: Icons.delete_outline,
                  accentColor: colors.danger,
                  enabled: !_busy,
                  onTap: _busy ? null : _delete,
                ),
              ],
            ),
    );
  }
}
