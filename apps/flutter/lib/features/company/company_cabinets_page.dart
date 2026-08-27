import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/company/company_cabinet_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Org cabinets list — metadata only, read-mostly (L04).
class CompanyCabinetsPage extends StatefulWidget {
  const CompanyCabinetsPage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyCabinetsPage> createState() => _CompanyCabinetsPageState();
}

class _CompanyCabinetsPageState extends State<CompanyCabinetsPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _cabinets = const [];

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

  int _count(dynamic value) {
    if (value is List) return value.length;
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse('$value') ?? 0;
  }

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final items = await companyContext.api.listOrgCabinets(widget.companyId);
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_cabinets, items) && !_loading) return;
      setState(() {
        _cabinets = items;
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

  void _openCabinet(AppEntityRow row) {
    Navigator.of(context)
        .push(
          MaterialPageRoute<void>(
            builder: (_) => CompanyCabinetDetailPage(
              companyId: widget.companyId,
              cabinetId: row.id,
              cabinetName: row.title,
            ),
          ),
        )
        .then((_) => _reload());
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _cabinets
        .map(
          (c) => AppEntityRow(
            id: c['id'] as String,
            title: c['name'] as String? ?? c['id'] as String,
            cells: {
              'employees': '${c['assignments_count'] ?? 0}',
              'modules': '${_count(c['module_bindings_count'])}',
            },
          ),
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
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              primaryColumnLabel: l10n.companyCabinet,
              columns: [
                AppEntityColumn(
                  id: 'employees',
                  label: l10n.companyAssignedEmployees,
                  width: 96,
                ),
                AppEntityColumn(id: 'modules', label: l10n.navModules, width: 96),
              ],
              onOpen: _openCabinet,
              empty: EmptyPlaceholder(
                title: l10n.companyNoCabinets,
                subtitle: l10n.companyCabinetsEmptyHint,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
