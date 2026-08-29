import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/company/company_container_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company project containers — employees' projects only (P-CO-03).
class CompanyProjectContainersPage extends StatefulWidget {
  const CompanyProjectContainersPage({
    super.key,
    required this.companyId,
    this.embedded = false,
  });

  final String companyId;
  final bool embedded;

  @override
  State<CompanyProjectContainersPage> createState() =>
      _CompanyProjectContainersPageState();
}

class _CompanyProjectContainersPageState extends State<CompanyProjectContainersPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  List<Map<String, dynamic>> _items = const [];

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
    if (!silent && mounted) setState(() => _loading = true);
    try {
      final items = await companyContext.api.listContainers(
        companyId: widget.companyId,
      );
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_items, items) && !_loading) return;
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _open(AppEntityRow row) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => CompanyContainerDetailPage(
          companyId: widget.companyId,
          projectId: row.id,
          projectName: row.title,
        ),
      ),
    );
    if (mounted) await _reload();
  }

  Future<void> _delete(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: l10n.adminDeleteContainerConfirm(row.title),
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    try {
      await companyContext.api.deleteContainer(
        companyId: widget.companyId,
        projectId: row.id,
      );
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  String _cell(dynamic v, AppLocalizations l10n) {
    if (v == null) return l10n.commonEmDash;
    final s = '$v'.trim();
    return s.isEmpty ? l10n.commonEmDash : s;
  }

  String _ownerLabel(Map<String, dynamic> item, AppLocalizations l10n) {
    final name = item['owner_display_name'] as String?;
    final email = item['owner_email'] as String?;
    if (name != null && name.trim().isNotEmpty) return name.trim();
    return _cell(email, l10n);
  }

  String _statusLabel(String? status, AppLocalizations l10n) {
    switch (status) {
      case 'active':
        return l10n.adminContainerStatusActive;
      case 'paused':
        return l10n.adminContainerStatusPaused;
      default:
        return _cell(status, l10n);
    }
  }

  Color? _statusColor(BuildContext context, String? status) {
    final colors = context.appColors;
    if (status == 'paused') return colors.warning;
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _items.map((item) {
      final status = item['status'] as String?;
      return AppEntityRow(
        id: item['id'] as String,
        title: item['project_name'] as String? ?? item['id'] as String,
        subtitle: _statusLabel(status, l10n),
        titleColor: _statusColor(context, status),
        cells: {
          'status': _statusLabel(status, l10n),
          'employee': _ownerLabel(item, l10n),
          'provider': _cell(item['agent_provider'], l10n),
          'cabinet': _cell(item['cabinet_name'], l10n),
        },
      );
    }).toList();

    return AppScaffold(
      title: widget.embedded ? null : Text(l10n.navContainers),
      body: AppEntityCollection(
        loading: _loading,
        rows: rows,
        primaryColumnLabel: l10n.adminContainerColProject,
        columns: [
          AppEntityColumn(id: 'status', label: l10n.adminContainerColStatus, flex: 1),
          AppEntityColumn(id: 'k8s', label: l10n.adminContainerColK8s, width: 120),
          AppEntityColumn(id: 'employee', label: l10n.adminContainerColEmployee, flex: 2),
          AppEntityColumn(id: 'provider', label: l10n.adminContainerColProvider, flex: 1),
          AppEntityColumn(id: 'cabinet', label: l10n.adminContainerColCabinet, flex: 2),
        ],
        onOpen: _open,
        onDelete: _delete,
        empty: EmptyPlaceholder(title: l10n.adminNoContainers),
      ),
    );
  }
}
