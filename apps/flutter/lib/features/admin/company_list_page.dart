import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_collection_view_mode.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin company list (L04).
class AdminCompanyListPage extends StatefulWidget {
  const AdminCompanyListPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminCompanyListPage> createState() => _AdminCompanyListPageState();
}

class _AdminCompanyListPageState extends State<AdminCompanyListPage> {
  static const _viewPageKey = 'admin.companies';

  final _viewMode = AppCollectionViewModeStore(_viewPageKey);
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  List<Map<String, dynamic>> _companies = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _reload(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _viewMode.load();
    _reload();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    _viewMode.dispose();
    super.dispose();
  }

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() => _loading = true);
    }
    try {
      final items = await adminContext.api.listCompanies();
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_companies, items) && !_loading) return;
      setState(() {
        _companies = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _createCompany(String name) async {
    final body = await adminContext.api.createCompany(name: name);
    if (!mounted) return;
    await _reload();
    if (!mounted) return;
    final company = body['company'] as Map<String, dynamic>? ?? body;
    final companyId = company['id'] as String?;
    final companyName = company['name'] as String? ?? name;
    if (companyId == null) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminCompanyDetailPage(
          companyId: companyId,
          companyName: companyName,
        ),
      ),
    );
    if (mounted) await _reload();
  }

  void _openCompany(AppEntityRow row) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminCompanyDetailPage(companyId: row.id, companyName: row.title),
      ),
    ).then((_) => _reload());
  }

  Future<void> _editCompany(AppEntityRow row) async {
    _openCompany(row);
  }

  Future<void> _deleteCompany(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: l10n.adminDeleteCompanyConfirm(row.title),
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    try {
      await adminContext.api.deleteCompany(row.id);
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _companies.map((c) {
      final running = c['running_cabinets'];
      final quota = c['cabinets_quota'] ?? c['cabinet_quota']?['max_cabinets'];
      final cabinetsCell = (running != null && quota != null)
          ? l10n.adminCompanyCabinetsRunning('$running', '$quota')
          : l10n.commonEmDash;
      final description = _cell(c['description'], l10n);
      return AppEntityRow(
        id: c['id'] as String,
        title: c['name'] as String? ?? c['id'] as String,
        subtitle: description != l10n.commonEmDash ? description : null,
        cells: {
          'description': description,
          'employees': _cell(c['employees_total'], l10n),
          'cabinets': cabinetsCell,
        },
      );
    }).toList();

    return ListenableBuilder(
      listenable: _viewMode,
      builder: (context, _) {
        return AppScaffold(
          actions: [
            AppCollectionViewModeButton(store: _viewMode),
          ],
          body: Column(
            children: [
              AppInlineAddField(
                title: l10n.adminAddCompany,
                hintText: l10n.adminAddCompany,
                validator: (v) => v.trim().isNotEmpty,
                invalidMessage: l10n.commonRequired,
                onSave: _createCompany,
              ),
              Expanded(
                child: AppEntityCollection(
                  loading: _loading,
                  mode: _viewMode.resolve(context),
                  rows: rows,
                  primaryColumnLabel: l10n.commonCompany,
                  columns: [
                    AppEntityColumn(id: 'description', label: l10n.commonDescription),
                    AppEntityColumn(
                      id: 'employees',
                      label: l10n.commonEmployees,
                      width: 110,
                      align: AppEntityColumnAlign.end,
                    ),
                    AppEntityColumn(
                      id: 'cabinets',
                      label: l10n.commonCabinets,
                      width: 100,
                      align: AppEntityColumnAlign.end,
                    ),
                  ],
                  onOpen: _openCompany,
                  onEdit: _editCompany,
                  onDelete: _deleteCompany,
                  empty: EmptyPlaceholder(
                    title: l10n.adminNoCompanies,
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
