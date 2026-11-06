import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_online_indicator.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/company/company_entity_source.dart';
import 'package:prodavan/features/company/company_employee_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company employees — inline create + table (Admin Companies parity).
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
  bool _showRecycleBin = false;
  String? _busyEmployeeId;
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
    final requestedRecycleBin = _showRecycleBin;
    try {
      final items = await companyContext.api.listEmployees(
        widget.companyId,
        includeDeleted: requestedRecycleBin,
      );
      if (!mounted || requestedRecycleBin != _showRecycleBin) return;
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

  static String _generatePassword() {
    const chars = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    final rand = Random.secure();
    return List.generate(16, (_) => chars[rand.nextInt(chars.length)]).join();
  }

  Future<void> _createEmployee(String login) async {
    final password = _generatePassword();
    final body = await companyContext.api.createEmployee(
      companyId: widget.companyId,
      login: login.trim(),
      password: password,
    );
    if (!mounted) return;
    await _reload();
    if (!mounted) return;
    final empId = body['id'] as String?;
    final empLogin = body['login'] as String? ?? login.trim();
    if (empId == null) return;
    await Clipboard.setData(ClipboardData(text: '$empLogin\t$password'));
    if (!mounted) return;
    final l10n = AppLocalizations.of(context);
    AppSnackBar.info(context, l10n.credentialsInClipboard);
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => CompanyEmployeeDetailPage(
          companyId: widget.companyId,
          employeeId: empId,
          employeeLogin: empLogin,
          status: body['status'] as String? ?? 'active',
        ),
      ),
    );
    if (mounted) await _reload();
  }

  Future<void> _restoreEmployee(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final emp = _employees.firstWhere((e) => e['id'] == row.id);
    final login = emp['login'] as String? ?? row.title;
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.companyRestoreEmployee,
      message: l10n.companyRestoreEmployeeConfirm(login),
      confirmLabel: l10n.companyRestoreEmployee,
    );
    if (!ok || !mounted) return;
    setState(() => _busyEmployeeId = row.id);
    try {
      await companyContext.api.restoreEmployee(
        companyId: widget.companyId,
        employeeId: row.id,
      );
      if (mounted) await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busyEmployeeId = null);
    }
  }

  void _openEmployee(AppEntityRow row) {
    final emp = _employees.firstWhere((e) => e['id'] == row.id);
    Navigator.of(context)
        .push(
          MaterialPageRoute<void>(
            builder: (_) => CompanyEmployeeDetailPage(
              companyId: widget.companyId,
              employeeId: row.id,
              employeeLogin: emp['login'] as String? ?? row.title,
              contactEmail: emp['contact_email'] as String?,
              status: emp['status'] as String? ?? 'active',
            ),
          ),
        )
        .then((_) => _reload());
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _employees
        .map(
          (e) {
            final deleted = e['deleted_at'] != null || _showRecycleBin;
            final disabled = e['status'] == 'disabled' || deleted;
            final style = companyEntityWarningRowStyle(context, warning: disabled);
            return AppEntityRow(
              id: e['id'] as String,
              title: e['login'] as String? ?? e['id'] as String,
              rowColor: style.rowColor,
              titleBold: style.titleBold,
              cells: {
                'email': e['contact_email'] as String? ?? '—',
                'projects': '${e['projects_count'] ?? 0}',
                'cabinets': '${e['cabinets_count'] ?? 0}',
              },
              cellWidgets: {
                'online': AppOnlineIndicator(online: e['online'] == true),
              },
              trailing: deleted
                  ? IconButton(
                      tooltip: l10n.companyRestoreEmployee,
                      icon: const Icon(Icons.restore_outlined),
                      onPressed: _busyEmployeeId == e['id']
                          ? null
                          : () => _restoreEmployee(
                                AppEntityRow(
                                  id: e['id'] as String,
                                  title: e['login'] as String? ?? e['id'] as String,
                                ),
                              ),
                    )
                  : null,
            );
          },
        )
        .toList();

    return AppScaffold(
      body: Column(
        children: [
          if (_error != null)
            AppStatusBanner(
              severity: AppStatusSeverity.error,
              message: AppErrors.localize(context, _error!),
            ),
          if (!_showRecycleBin)
            AppInlineAddField(
              title: l10n.companyAddEmployee,
              hintText: l10n.companyAddEmployeeHint,
              validator: (v) => v.trim().length >= 3,
              invalidMessage: l10n.companyPasswordHint,
              onSave: _createEmployee,
            ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: SegmentedButton<bool>(
              segments: [
                ButtonSegment<bool>(
                  value: false,
                  label: Text(l10n.companyEmployeesTab),
                  icon: const Icon(Icons.group_outlined),
                ),
                ButtonSegment<bool>(
                  value: true,
                  label: Text(l10n.companyEmployeeRecycleBin),
                  icon: const Icon(Icons.delete_outline),
                ),
              ],
              selected: {_showRecycleBin},
              onSelectionChanged: (selected) {
                setState(() {
                  _showRecycleBin = selected.single;
                  _employees = const [];
                });
                _reload();
              },
            ),
          ),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              primaryColumnLabel: l10n.companyLogin,
              columns: [
                AppEntityColumn(
                  id: 'online',
                  label: l10n.commonOnline,
                  width: 72,
                  align: AppEntityColumnAlign.center,
                ),
                AppEntityColumn(id: 'email', label: l10n.commonEmail),
                AppEntityColumn(
                  id: 'projects',
                  label: l10n.commonProjects,
                  width: 96,
                  align: AppEntityColumnAlign.center,
                ),
                AppEntityColumn(
                  id: 'cabinets',
                  label: l10n.commonCabinets,
                  width: 96,
                  align: AppEntityColumnAlign.center,
                ),
              ],
              rowActions: [
                AppEntityRowAction(
                  tooltip: l10n.companyRestoreEmployee,
                  icon: Icons.restore_outlined,
                  visible: (_) => _showRecycleBin,
                  onPressed: _restoreEmployee,
                ),
              ],
              onOpen: _showRecycleBin ? (_) {} : _openEmployee,
              empty: EmptyPlaceholder(
                title: _showRecycleBin
                    ? l10n.companyNoDeletedEmployees
                    : l10n.companyNoEmployees,
                icon: _showRecycleBin ? Icons.delete_outline : Icons.group_outlined,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
