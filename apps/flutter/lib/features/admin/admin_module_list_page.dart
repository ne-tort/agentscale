import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/admin/admin_module_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin module list — create, open, copy id, delete.
class AdminModuleListPage extends StatefulWidget {
  const AdminModuleListPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminModuleListPage> createState() => _AdminModuleListPageState();
}

class _AdminModuleListPageState extends State<AdminModuleListPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  List<Map<String, dynamic>> _modules = const [];

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
      final items = await adminContext.api.listModules();
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_modules, items) && !_loading) return;
      setState(() {
        _modules = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _createModule(String name) async {
    try {
      final body = await adminContext.api.createModule(name: name);
      if (!mounted) return;
      await _reload();
      if (!mounted) return;
      final id = body['id'] as String?;
      final moduleName = body['name'] as String? ?? name;
      if (id == null) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => AdminModuleDetailPage(
            moduleId: id,
            moduleName: moduleName,
          ),
        ),
      );
      if (mounted) await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  void _openModule(AppEntityRow row) {
    Navigator.of(context)
        .push(
          MaterialPageRoute<void>(
            builder: (_) => AdminModuleDetailPage(
              moduleId: row.id,
              moduleName: row.title,
            ),
          ),
        )
        .then((_) => _reload());
  }

  Future<void> _copyModule(AppEntityRow row) async {
    try {
      final body = await adminContext.api.copyModule(moduleId: row.id);
      if (!mounted) return;
      await _reload();
      if (!mounted) return;
      final id = body['id'] as String?;
      final moduleName = body['name'] as String? ?? row.title;
      if (id == null) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => AdminModuleDetailPage(
            moduleId: id,
            moduleName: moduleName,
          ),
        ),
      );
      if (mounted) await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _deleteModule(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: l10n.adminDeleteModuleConfirm(row.title),
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    try {
      await adminContext.api.deleteModule(row.id);
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  bool _isProductModule(Map<String, dynamic> m) =>
      m['company_grant_scope'] == 'all';

  bool _rowDeletable(AppEntityRow row) {
    final mod = _modules.firstWhere((m) => m['id'] == row.id, orElse: () => const {});
    return mod.isNotEmpty && !_isProductModule(mod);
  }

  int _countIds(dynamic value) {
    if (value is List) return value.length;
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse('$value') ?? 0;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _modules
        .map(
          (m) {
            final cabinets = _countIds(
              m['cabinet_bindings_count'] ?? m['cabinet_ids'],
            );
            final companies = _countIds(
              m['company_grants_count'] ?? m['company_ids'],
            );
            return AppEntityRow(
              id: m['id'] as String,
              title: m['name'] as String? ?? m['id'] as String,
              cells: {
                'cabinets': '$cabinets',
                'companies': '$companies',
              },
            );
          },
        )
        .toList();

    return AppScaffold(
      title: widget.embedded ? null : Text(l10n.navModules),
      body: Column(
        children: [
          AppInlineAddField(
            title: l10n.adminAddModule,
            hintText: l10n.adminAddModule,
            validator: (v) => v.trim().isNotEmpty,
            invalidMessage: l10n.commonRequired,
            onSave: _createModule,
          ),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              mode: AppEntityCollectionMode.table,
              primaryColumnLabel: l10n.navModules,
              columns: [
                AppEntityColumn(id: 'cabinets', label: l10n.commonCabinets),
                AppEntityColumn(id: 'companies', label: l10n.navCompanies),
              ],
              onOpen: _openModule,
              onCopy: _copyModule,
              onDelete: _deleteModule,
              deletableOf: _rowDeletable,
              copyableOf: (_) => true,
              empty: EmptyPlaceholder(
                title: l10n.adminNoModules,
                icon: Icons.extension_outlined,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
