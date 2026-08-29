import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
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
            final disabled = e['status'] == 'disabled';
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
          AppInlineAddField(
            title: l10n.companyAddEmployee,
            hintText: l10n.companyAddEmployee,
            validator: (v) => v.trim().length >= 3,
            invalidMessage: l10n.companyPasswordHint,
            onSave: _createEmployee,
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
              onOpen: _openEmployee,
              empty: EmptyPlaceholder(
                title: l10n.companyNoEmployees,
                icon: Icons.group_outlined,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
