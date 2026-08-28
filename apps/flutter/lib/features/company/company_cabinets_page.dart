import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/company/company_cabinet_detail_page.dart';
import 'package:prodavan/features/company/company_entity_source.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Org cabinets list — inline create, copy, delete local cabinets.
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

  String _projectsLabel(Map<String, dynamic> c) {
    final count = _count(c['projects_count']);
    final limit = c['max_projects'];
    if (limit == null) return '$count';
    return '$count/${_count(limit)}';
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

  Future<void> _createCabinet(String name) async {
    try {
      final body = await companyContext.api.createCabinet(
        companyId: widget.companyId,
        name: name.trim(),
      );
      if (!mounted) return;
      await _reload();
      if (!mounted) return;
      final id = body['id'] as String?;
      final cabinetName = body['name'] as String? ?? name.trim();
      if (id == null) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => CompanyCabinetDetailPage(
            companyId: widget.companyId,
            cabinetId: id,
            cabinetName: cabinetName,
          ),
        ),
      );
      if (mounted) await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
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

  Future<void> _copyCabinet(AppEntityRow row) async {
    try {
      final body = await companyContext.api.copyCabinet(
        companyId: widget.companyId,
        cabinetId: row.id,
      );
      if (!mounted) return;
      await _reload();
      if (!mounted) return;
      final id = body['id'] as String?;
      final cabinetName = body['name'] as String? ?? row.title;
      if (id == null) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => CompanyCabinetDetailPage(
            companyId: widget.companyId,
            cabinetId: id,
            cabinetName: cabinetName,
          ),
        ),
      );
      if (mounted) await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _deleteCabinet(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: row.title,
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    try {
      await companyContext.api.deleteCabinet(row.id);
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  bool _rowWritable(AppEntityRow row) {
    final cab = _cabinets.firstWhere((c) => c['id'] == row.id, orElse: () => const {});
    return cab['writable'] == true;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _cabinets
        .map(
          (c) {
            final style = companyEntityRowStyle(context, c['source'] as String?);
            return AppEntityRow(
              id: c['id'] as String,
              title: c['name'] as String? ?? c['id'] as String,
              rowColor: style.rowColor,
              titleBold: style.titleBold,
              cells: {
                'employees': '${c['assignments_count'] ?? 0}',
                'projects': _projectsLabel(c),
                'modules': '${_count(c['module_bindings_count'])}',
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
            title: l10n.companyAddCabinet,
            hintText: l10n.companyAddCabinet,
            validator: (v) => v.trim().isNotEmpty,
            invalidMessage: l10n.commonRequired,
            onSave: _createCabinet,
          ),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              primaryColumnLabel: l10n.companyCabinet,
              columns: [
                AppEntityColumn(
                  id: 'employees',
                  label: l10n.commonEmployees,
                  width: 96,
                  align: AppEntityColumnAlign.center,
                ),
                AppEntityColumn(
                  id: 'projects',
                  label: l10n.commonProjects,
                  width: 96,
                  align: AppEntityColumnAlign.center,
                ),
                AppEntityColumn(
                  id: 'modules',
                  label: l10n.navModules,
                  width: 96,
                  align: AppEntityColumnAlign.center,
                ),
              ],
              onOpen: _openCabinet,
              onCopy: _copyCabinet,
              onDelete: _deleteCabinet,
              copyableOf: (_) => true,
              deletableOf: _rowWritable,
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
