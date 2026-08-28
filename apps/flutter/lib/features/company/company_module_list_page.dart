import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/company/company_entity_source.dart';
import 'package:prodavan/features/company/company_module_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company modules — local CRUD + platform-assigned RO + cabinet bind.
class CompanyModuleListPage extends StatefulWidget {
  const CompanyModuleListPage({super.key, required this.companyId, this.embedded = false});

  final String companyId;
  final bool embedded;

  @override
  State<CompanyModuleListPage> createState() => _CompanyModuleListPageState();
}

class _CompanyModuleListPageState extends State<CompanyModuleListPage> {
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
    if (!silent && mounted) setState(() => _loading = true);
    try {
      final items = await companyContext.api.listModules(widget.companyId);
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
      final body = await companyContext.api.createModule(
        companyId: widget.companyId,
        name: name,
      );
      if (!mounted) return;
      await _reload();
      if (!mounted) return;
      final id = body['id'] as String?;
      final moduleName = body['name'] as String? ?? name;
      if (id == null) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => CompanyModuleDetailPage(
            companyId: widget.companyId,
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
            builder: (_) => CompanyModuleDetailPage(
              companyId: widget.companyId,
              moduleId: row.id,
              moduleName: row.title,
            ),
          ),
        )
        .then((_) => _reload());
  }

  Future<void> _deleteModule(AppEntityRow row) async {
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
      await companyContext.api.deleteModule(
        companyId: widget.companyId,
        moduleId: row.id,
      );
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _copyModule(AppEntityRow row) async {
    try {
      final body = await companyContext.api.copyModule(
        companyId: widget.companyId,
        moduleId: row.id,
      );
      if (!mounted) return;
      await _reload();
      if (!mounted) return;
      final id = body['id'] as String?;
      final moduleName = body['name'] as String? ?? row.title;
      if (id == null) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => CompanyModuleDetailPage(
            companyId: widget.companyId,
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

  bool _rowWritable(AppEntityRow row) {
    final mod = _modules.firstWhere((m) => m['id'] == row.id, orElse: () => const {});
    return mod['writable'] == true;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _modules.map((m) {
      final bindCount = m['cabinet_bindings_count'] as int? ?? 0;
      final style = companyEntityRowStyle(context, m['source'] as String?);
      return AppEntityRow(
        id: m['id'] as String,
        title: m['name'] as String? ?? m['id'] as String,
        rowColor: style.rowColor,
        titleBold: style.titleBold,
        cells: {
          'cabinets': '$bindCount',
        },
      );
    }).toList();

    return AppScaffold(
      title: widget.embedded ? null : Text(l10n.navModules),
      body: Column(
        children: [
          AppInlineAddField(
            title: l10n.companyAddModule,
            hintText: l10n.companyAddModule,
            validator: (v) => v.trim().isNotEmpty,
            invalidMessage: l10n.commonRequired,
            onSave: _createModule,
          ),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              primaryColumnLabel: l10n.commonName,
              columns: [
                AppEntityColumn(
                  id: 'cabinets',
                  label: l10n.commonCabinets,
                  width: 96,
                  align: AppEntityColumnAlign.center,
                ),
              ],
              onOpen: _openModule,
              onCopy: _copyModule,
              onDelete: _deleteModule,
              copyableOf: (_) => true,
              deletableOf: _rowWritable,
              empty: EmptyPlaceholder(
                title: l10n.companyNoModules,
                subtitle: l10n.companyModulesEmptyHint,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
