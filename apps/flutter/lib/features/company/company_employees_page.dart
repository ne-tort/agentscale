import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/company/company_invite_employee_page.dart';
import 'package:prodavan/features/company/company_employee_cabinets_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company employees — invite + disable (L04). No static cabinet grants.
class CompanyEmployeesPage extends StatefulWidget {
  const CompanyEmployeesPage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyEmployeesPage> createState() => _CompanyEmployeesPageState();
}

class _CompanyEmployeesPageState extends State<CompanyEmployeesPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _employees = const [];

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
      final items = await companyContext.api.listEmployees(widget.companyId);
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_employees, items) && !_loading) return;
      setState(() {
        _employees = items;
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

  Future<void> _invite() async {
    final invited = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => CompanyInviteEmployeePage(companyId: widget.companyId),
      ),
    );
    if (invited == true) await _reload();
  }

  Future<void> _disable(Map<String, dynamic> emp) async {
    final l10n = AppLocalizations.of(context);
    if (emp['status'] == 'disabled') return;
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.companyDisableEmployee,
      message: l10n.companyDisableEmployeeConfirm('${emp['email']}'),
      confirmLabel: l10n.commonDisable,
      severity: AppStatusSeverity.warning,
    );
    if (!ok) return;
    try {
      await companyContext.api.disableEmployee(emp['id'] as String);
      await _reload();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _employees
        .map(
          (e) => AppEntityRow(
            id: e['id'] as String,
            title: e['email'] as String? ?? e['id'] as String,
            subtitle: '${e['role']} · ${e['status']}',
            cells: {
              'role': e['role'] as String? ?? '—',
              'status': e['status'] as String? ?? '—',
            },
            trailing: e['status'] == 'disabled'
                ? null
                : IconButton(
                    icon: const Icon(Icons.block),
                    tooltip: l10n.commonDisable,
                    onPressed: () => _disable(e),
                  ),
          ),
        )
        .toList();

    return AppScaffold(
      actions: [
        AppIconButton(
          icon: Icons.person_add,
          tooltip: l10n.commonInvite,
          onPressed: _invite,
        ),
      ],
      body: Column(
        children: [
          if (_error != null)
            AppStatusBanner(
              severity: AppStatusSeverity.error,
              message: AppErrors.localize(context, _error!),
            ),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              primaryColumnLabel: l10n.commonEmail,
              columns: [
                AppEntityColumn(id: 'email', label: l10n.commonEmail),
                AppEntityColumn(id: 'role', label: l10n.companyRole),
                AppEntityColumn(id: 'status', label: l10n.commonStatus),
              ],
              onOpen: (row) {
                Navigator.of(context)
                    .push(
                      MaterialPageRoute<void>(
                        builder: (_) => CompanyEmployeeCabinetsPage(
                          companyId: widget.companyId,
                          employeeId: row.id,
                          employeeEmail: row.title,
                        ),
                      ),
                    )
                    .then((_) => _reload());
              },
              empty: EmptyPlaceholder(
                title: l10n.companyNoEmployees,
                subtitle: l10n.companyInviteViaKeycloakNoPassword,
                action: TextButton(onPressed: _invite, child: Text(l10n.commonInvite)),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
