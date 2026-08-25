import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/company/company_invite_employee_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company employees — invite + disable (L04). No static cabinet grants.
class CompanyEmployeesPage extends StatefulWidget {
  const CompanyEmployeesPage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyEmployeesPage> createState() => _CompanyEmployeesPageState();
}

class _CompanyEmployeesPageState extends State<CompanyEmployeesPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _employees = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await companyContext.api.listEmployees(widget.companyId);
      if (!mounted) return;
      setState(() {
        _employees = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
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
    final ok = await DangerConfirmPage.push(
      context,
      title: l10n.companyDisableEmployee,
      message: l10n.companyDisableEmployeeConfirm('${emp['email']}'),
      confirmLabel: l10n.commonDisable,
    );
    if (!ok) return;
    try {
      await companyContext.api.disableEmployee(emp['id'] as String);
      await _reload();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
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
                    icon: Icon(Icons.block),
                    tooltip: l10n.commonDisable,
                    onPressed: () => _disable(e),
                  ),
          ),
        )
        .toList();

    return Column(
      children: [
        if (_error != null) InlineErrorBanner(message: _error!),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              onPressed: _invite,
              icon: Icon(Icons.person_add),
              label: Text(l10n.commonInvite),
            ),
          ),
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
            onOpen: (_) {},
            empty: EmptyPlaceholder(
              title: l10n.companyNoEmployees,
              subtitle: l10n.companyInviteViaKeycloakNoPassword,
              action: TextButton(onPressed: _invite, child: Text(l10n.commonInvite)),
            ),
          ),
        ),
      ],
    );
  }
}
