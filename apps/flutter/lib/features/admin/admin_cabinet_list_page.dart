import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/admin/admin_cabinet_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin cabinet list — create, open, copy id, delete.
class AdminCabinetListPage extends StatefulWidget {
  const AdminCabinetListPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminCabinetListPage> createState() => _AdminCabinetListPageState();
}

class _AdminCabinetListPageState extends State<AdminCabinetListPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
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

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() => _loading = true);
    }
    try {
      final items = await adminContext.api.listCabinets();
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_cabinets, items) && !_loading) return;
      setState(() {
        _cabinets = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _createCabinet(String name) async {
    final l10n = AppLocalizations.of(context);
    List<Map<String, dynamic>> companies;
    try {
      companies = await adminContext.api.listCompanies();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      return;
    }
    if (!mounted) return;
    if (companies.isEmpty) {
      AppSnackBar.info(context, l10n.adminNoCompanies);
      return;
    }

    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppCatalogSelectPage(
          title: l10n.adminSelectCompanyForCabinet,
          multiSelect: true,
          items: [
            for (final c in companies)
              AppCatalogSelectItem(
                id: c['id'] as String,
                title: c['name'] as String? ?? c['id'] as String,
              ),
          ],
        ),
      ),
    );
    if (picked == null || picked.isEmpty || !mounted) return;

    try {
      final body = await adminContext.api.createCabinet(
        name: name,
        companyIds: picked.toList(),
      );
      if (!mounted) return;
      await _reload();
      if (!mounted) return;
      final id = body['id'] as String?;
      final cabinetName = body['name'] as String? ?? name;
      if (id == null) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => AdminCabinetDetailPage(
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
            builder: (_) => AdminCabinetDetailPage(
              cabinetId: row.id,
              cabinetName: row.title,
            ),
          ),
        )
        .then((_) => _reload());
  }

  Future<void> _copyCabinet(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    await Clipboard.setData(ClipboardData(text: row.id));
    if (!mounted) return;
    AppSnackBar.info(context, l10n.adminCabinetCopied);
  }

  Future<void> _deleteCabinet(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: l10n.adminDeleteCabinetConfirm(row.title),
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    try {
      await adminContext.api.deleteCabinet(row.id);
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _cabinets
        .map(
          (c) {
            final companyIds = c['company_ids'];
            final count = companyIds is List ? companyIds.length : 0;
            return AppEntityRow(
              id: c['id'] as String,
              title: c['name'] as String? ?? c['id'] as String,
              cells: {
                'companies': count > 0 ? '$count' : l10n.commonNotSet,
                'scope': c['owner_scope'] as String? ?? '—',
                'status': c['status'] as String? ?? '',
              },
            );
          },
        )
        .toList();

    return AppScaffold(
      title: widget.embedded ? null : Text(l10n.navCabinets),
      body: Column(
        children: [
          AppInlineAddField(
            title: l10n.adminAddCabinet,
            hintText: l10n.adminAddCabinet,
            validator: (v) => v.trim().isNotEmpty,
            invalidMessage: l10n.commonRequired,
            onSave: _createCabinet,
          ),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              primaryColumnLabel: l10n.commonCabinets,
              columns: [
                AppEntityColumn(id: 'companies', label: l10n.commonCompanies),
                AppEntityColumn(id: 'scope', label: l10n.adminCabinetOwnerScope, width: 100),
                AppEntityColumn(
                  id: 'status',
                  label: l10n.commonStatus,
                  width: 100,
                ),
              ],
              onOpen: _openCabinet,
              onCopy: _copyCabinet,
              onDelete: _deleteCabinet,
              empty: EmptyPlaceholder(
                title: l10n.adminNoCabinets,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
