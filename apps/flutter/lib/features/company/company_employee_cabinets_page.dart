import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Assign org cabinets to a single employee.
class CompanyEmployeeCabinetsPage extends StatefulWidget {
  const CompanyEmployeeCabinetsPage({
    super.key,
    required this.companyId,
    required this.employeeId,
    required this.employeeEmail,
  });

  final String companyId;
  final String employeeId;
  final String employeeEmail;

  @override
  State<CompanyEmployeeCabinetsPage> createState() =>
      _CompanyEmployeeCabinetsPageState();
}

class _CompanyEmployeeCabinetsPageState extends State<CompanyEmployeeCabinetsPage> {
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _cabinets = const [];
  final Set<String> _assigned = {};

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
      final cabinets = await companyContext.api.listOrgCabinets(widget.companyId);
      final assigned = <String>{};
      for (final cab in cabinets) {
        final cabId = cab['id'] as String;
        final items = await companyContext.api.listCabinetAssignments(
          companyId: widget.companyId,
          cabinetId: cabId,
        );
        if (items.any((a) => a['employee_id'] == widget.employeeId)) {
          assigned.add(cabId);
        }
      }
      if (!mounted) return;
      setState(() {
        _cabinets = cabinets;
        _assigned
          ..clear()
          ..addAll(assigned);
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

  Future<void> _toggle(String cabinetId, bool assign) async {
    try {
      if (assign) {
        await companyContext.api.assignCabinetEmployee(
          companyId: widget.companyId,
          cabinetId: cabinetId,
          employeeId: widget.employeeId,
        );
        setState(() => _assigned.add(cabinetId));
      } else {
        await companyContext.api.revokeCabinetAssignment(
          companyId: widget.companyId,
          cabinetId: cabinetId,
          employeeId: widget.employeeId,
        );
        setState(() => _assigned.remove(cabinetId));
      }
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(widget.employeeEmail),
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
                Padding(
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.md,
                    AppSpacing.sm,
                    AppSpacing.md,
                    AppSpacing.xs,
                  ),
                  child: Text(
                    l10n.companyAssignCabinetsToEmployee,
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                ),
                if (_cabinets.isEmpty)
                  ListTile(title: Text(l10n.companyNoCabinets))
                else
                  for (final cab in _cabinets)
                    SwitchListTile(
                      title: Text(cab['name'] as String? ?? cab['id'] as String),
                      subtitle: Text(cab['owner_scope'] as String? ?? '—'),
                      value: _assigned.contains(cab['id'] as String),
                      onChanged: (v) => _toggle(cab['id'] as String, v),
                    ),
              ],
            ),
    );
  }
}
