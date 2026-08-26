import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company cabinet detail — read-only metadata + employee assignments.
class CompanyCabinetDetailPage extends StatefulWidget {
  const CompanyCabinetDetailPage({
    super.key,
    required this.companyId,
    required this.cabinetId,
    required this.cabinetName,
    this.writable = false,
    this.ownerScope = 'platform',
  });

  final String companyId;
  final String cabinetId;
  final String cabinetName;
  final bool writable;
  final String ownerScope;

  @override
  State<CompanyCabinetDetailPage> createState() => _CompanyCabinetDetailPageState();
}

class _CompanyCabinetDetailPageState extends State<CompanyCabinetDetailPage> {
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _assignments = const [];
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
      final assignments = await companyContext.api.listCabinetAssignments(
        companyId: widget.companyId,
        cabinetId: widget.cabinetId,
      );
      final employees = await companyContext.api.listEmployees(widget.companyId);
      if (!mounted) return;
      setState(() {
        _assignments = assignments;
        _employees = employees;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Set<String> get _assignedIds => _assignments
      .map((a) => a['employee_id'] as String?)
      .whereType<String>()
      .toSet();

  Future<void> _assignEmployee() async {
    final l10n = AppLocalizations.of(context);
    final assigned = _assignedIds;
    final available = _employees
        .where((e) => e['status'] != 'disabled')
        .where((e) => !assigned.contains(e['id'] as String))
        .toList();
    if (available.isEmpty) {
      AppSnackBar.info(context, l10n.companyNoEmployees);
      return;
    }
    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppCatalogSelectPage(
          title: l10n.companyAssignEmployeeToCabinet,
          items: [
            for (final e in available)
              AppCatalogSelectItem(
                id: e['id'] as String,
                title: e['email'] as String? ?? e['id'] as String,
              ),
          ],
        ),
      ),
    );
    if (picked == null || picked.isEmpty || !mounted) return;
    try {
      await companyContext.api.assignCabinetEmployee(
        companyId: widget.companyId,
        cabinetId: widget.cabinetId,
        employeeId: picked.first,
      );
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _revoke(String employeeId) async {
    try {
      await companyContext.api.revokeCabinetAssignment(
        companyId: widget.companyId,
        cabinetId: widget.cabinetId,
        employeeId: employeeId,
      );
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(widget.cabinetName),
      actions: [
        IconButton(
          icon: const Icon(Icons.person_add_outlined),
          tooltip: l10n.companyAssignEmployeeToCabinet,
          onPressed: _loading ? null : _assignEmployee,
        ),
      ],
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: [
                if (_error != null)
                  AppStatusBanner(
                    severity: AppStatusSeverity.error,
                    message: AppErrors.localize(context, _error!),
                  ),
                if (!widget.writable)
                  AppStatusBanner(
                    severity: AppStatusSeverity.info,
                    message: l10n.companyReadOnlyOrgView(widget.cabinetName),
                  ),
                ListTile(
                  leading: const Icon(Icons.shield_outlined),
                  title: Text(l10n.adminCabinetOwnerScope),
                  subtitle: Text(widget.ownerScope),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.md,
                    AppSpacing.sm,
                    AppSpacing.md,
                    AppSpacing.xs,
                  ),
                  child: Text(
                    l10n.companyAssignedEmployees,
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                ),
                if (_assignments.isEmpty)
                  ListTile(
                    title: Text(l10n.companyNoAssignedEmployees),
                  )
                else
                  for (final a in _assignments)
                    ListTile(
                      leading: const Icon(Icons.person_outline),
                      title: Text(
                        a['employee_email'] as String? ??
                            a['employee_id'] as String? ??
                            '—',
                      ),
                      trailing: IconButton(
                        icon: const Icon(Icons.remove_circle_outline),
                        tooltip: l10n.commonRemove,
                        onPressed: () {
                          final id = a['employee_id'] as String?;
                          if (id != null) _revoke(id);
                        },
                      ),
                    ),
              ],
            ),
    );
  }
}
