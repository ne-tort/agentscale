import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/employee/cabinet_column_add_page.dart';
import 'package:prodavan/features/employee/cabinet_column_edit_page.dart';
import 'package:prodavan/features/employee/cabinet_row_edit_page.dart';
import 'package:prodavan/features/employee/cabinet_table_settings_page.dart';
import 'package:prodavan/features/employee/cabinet_table_create_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Meta tables browser with row upsert/delete (L05/L06 interpreter).
class CabinetTablesTabPage extends StatefulWidget {
  const CabinetTablesTabPage({
    super.key,
    required this.cabinetId,
    this.initialTableSlug,
  });

  final String cabinetId;
  final String? initialTableSlug;

  @override
  State<CabinetTablesTabPage> createState() => _CabinetTablesTabPageState();
}

class _CabinetTablesTabPageState extends State<CabinetTablesTabPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _tables = const [];
  String? _selectedSlug;
  List<Map<String, dynamic>> _rows = const [];
  List<Map<String, dynamic>> _columns = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _silentRefresh(),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _loadTables().then((_) {
      final slug = widget.initialTableSlug;
      if (slug != null && slug.isNotEmpty && mounted) {
        _loadRows(slug);
      }
    });
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _silentRefresh() async {
    final tables = await workContext.api.listMetaTables(widget.cabinetId);
    if (!mounted) return;
    final slug = _selectedSlug;
    List<Map<String, dynamic>>? nextRows;
    List<Map<String, dynamic>>? nextColumns;
    if (slug != null && slug.isNotEmpty) {
      final meta = await workContext.api.getMetaTable(
        cabinetId: widget.cabinetId,
        tableSlug: slug,
      );
      final result = await workContext.api.queryCabinetRows(
        cabinetId: widget.cabinetId,
        tableSlug: slug,
      );
      if (!mounted) return;
      final cols = meta['columns'];
      final rows = result['rows'];
      nextColumns =
          cols is List ? cols.cast<Map<String, dynamic>>() : const [];
      nextRows = rows is List ? rows.cast<Map<String, dynamic>>() : const [];
    }
    if (appRefreshDataEquals(_tables, tables) &&
        (slug == null ||
            (appRefreshDataEquals(_rows, nextRows) &&
                appRefreshDataEquals(_columns, nextColumns))) &&
        !_loading) {
      return;
    }
    setState(() {
      _tables = tables;
      if (nextRows != null) _rows = nextRows;
      if (nextColumns != null) _columns = nextColumns;
      _loading = false;
    });
  }

  Future<void> _loadTables() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final tables = await workContext.api.listMetaTables(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _tables = tables;
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

  Future<void> _loadRows(String slug) async {
    setState(() {
      _selectedSlug = slug;
      _error = null;
      _rows = const [];
    });
    try {
      final meta = await workContext.api.getMetaTable(
        cabinetId: widget.cabinetId,
        tableSlug: slug,
      );
      final result = await workContext.api.queryCabinetRows(
        cabinetId: widget.cabinetId,
        tableSlug: slug,
      );
      if (!mounted) return;
      final rows = result['rows'];
      final cols = meta['columns'];
      setState(() {
        _columns = cols is List ? cols.cast<Map<String, dynamic>>() : const [];
        _rows = rows is List ? rows.cast<Map<String, dynamic>>() : const [];
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e);
    }
  }

  Future<void> _tableSettings() async {
    final slug = _selectedSlug;
    if (slug == null) return;

    final table = _tables.cast<Map<String, dynamic>?>().firstWhere(
          (t) => t?['slug'] == slug,
          orElse: () => null,
        );
    final label = table?['label'] as String? ?? slug;

    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => CabinetTableSettingsPage(
          cabinetId: widget.cabinetId,
          tableSlug: slug,
          tableLabel: label,
        ),
      ),
    );
    if (changed == true) {
      await _loadTables();
      if (_selectedSlug == slug) {
        setState(() => _selectedSlug = null);
      }
    }
  }

  Future<void> _addColumn() async {
    final slug = _selectedSlug;
    if (slug == null) return;

    final table = _tables.cast<Map<String, dynamic>?>().firstWhere(
          (t) => t?['slug'] == slug,
          orElse: () => null,
        );
    final label = table?['label'] as String? ?? slug;

    final added = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => CabinetColumnAddPage(
          cabinetId: widget.cabinetId,
          tableSlug: slug,
          tableLabel: label,
        ),
      ),
    );
    if (added == true) {
      await _loadRows(slug);
    }
  }

  Future<void> _editColumn(Map<String, dynamic> column) async {
    final slug = _selectedSlug;
    if (slug == null) return;

    final table = _tables.cast<Map<String, dynamic>?>().firstWhere(
          (t) => t?['slug'] == slug,
          orElse: () => null,
        );
    final label = table?['label'] as String? ?? slug;

    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => CabinetColumnEditPage(
          cabinetId: widget.cabinetId,
          tableSlug: slug,
          tableLabel: label,
          column: column,
        ),
      ),
    );
    if (changed == true) {
      await _loadRows(slug);
    }
  }

  Future<void> _createTable() async {
    final created = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => CabinetTableCreatePage(cabinetId: widget.cabinetId),
      ),
    );
    if (created == true) {
      await _loadTables();
    }
  }

  Future<void> _editRow({Map<String, dynamic>? existing}) async {
    final slug = _selectedSlug;
    if (slug == null) return;

    final table = _tables.cast<Map<String, dynamic>?>().firstWhere(
          (t) => t?['slug'] == slug,
          orElse: () => null,
        );
    final label = table?['label'] as String? ?? slug;

    final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => CabinetRowEditPage(
          cabinetId: widget.cabinetId,
          tableSlug: slug,
          tableLabel: label,
          columns: _columns,
          existing: existing,
        ),
      ),
    );
    if (saved == true) {
      await _loadRows(slug);
    }
  }

  Future<void> _deleteRow(Map<String, dynamic> row) async {
    final l10n = AppLocalizations.of(context);
    final slug = _selectedSlug;
    final rowId = row['id'] as String?;
    if (slug == null || rowId == null) return;

    final ok = await AppConfirmPage.push(
      context,
      title: l10n.cabinetDeleteRow,
      message: l10n.cabinetDeleteRowPermanently('$rowId'),
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (ok != true) return;

    try {
      await workContext.api.deleteCabinetRow(
        cabinetId: widget.cabinetId,
        tableSlug: slug,
        rowId: rowId,
      );
      await _loadRows(slug);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_tables.isEmpty) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
          Expanded(
            child: EmptyPlaceholder(title: l10n.cabinetNoMetaTablesYet),
          ),
          Padding(
            padding: EdgeInsets.all(12),
            child: TextButton.icon(
              onPressed: _createTable,
              icon: Icon(Icons.add),
              label: Text(l10n.cabinetNewTable),
            ),
          ),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              onPressed: _createTable,
              icon: Icon(Icons.table_rows),
              label: Text(l10n.cabinetNewTable),
            ),
          ),
        ),
        SizedBox(
          height: 52,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            itemCount: _tables.length,
            separatorBuilder: (_, __) => const SizedBox(width: 8),
            itemBuilder: (context, index) {
              final table = _tables[index];
              final slug = table['slug'] as String? ?? '';
              final label = table['label'] as String? ?? slug;
              final selected = _selectedSlug == slug;
              return FilterChip(
                label: Text(label),
                selected: selected,
                onSelected: (_) => _loadRows(slug),
              );
            },
          ),
        ),
        const Divider(height: 1),
        if (_selectedSlug != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton.icon(
                  onPressed: _tableSettings,
                  icon: Icon(Icons.settings_outlined),
                  label: Text(l10n.settings),
                ),
                const SizedBox(width: 8),
                TextButton.icon(
                  onPressed: _addColumn,
                  icon: Icon(Icons.view_column_outlined),
                  label: Text(l10n.cabinetAddColumn),
                ),
                const SizedBox(width: 8),
                TextButton.icon(
                  onPressed: () => _editRow(),
                  icon: Icon(Icons.add),
                  label: Text(l10n.cabinetAddRow),
                ),
              ],
            ),
          ),
        if (_selectedSlug != null && _columns.isNotEmpty)
          SizedBox(
            height: 44,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              itemCount: _columns.length,
              separatorBuilder: (_, __) => const SizedBox(width: 6),
              itemBuilder: (context, index) {
                final col = _columns[index];
                final name = col['name'] as String? ?? '';
                final type = col['type'] as String? ?? '';
                return ActionChip(
                  label: Text(l10n.cabinetColumnTypeChip(name, type)),
                  onPressed: () => _editColumn(col),
                );
              },
            ),
          ),
        Expanded(
          child: _selectedSlug == null
              ? EmptyPlaceholder(
                  title: l10n.cabinetSelectTableToPreview,
                  icon: Icons.table_chart_outlined,
                )
              : _rows.isEmpty
                  ? EmptyPlaceholder(
                      title: l10n.cabinetNoRows,
                      icon: Icons.table_rows_outlined,
                      onTitleTap: () => _editRow(),
                      action: TextButton.icon(
                        onPressed: () => _editRow(),
                        icon: const Icon(Icons.add),
                        label: Text(l10n.cabinetAddRow),
                      ),
                    )
                  : ListView.separated(
                      padding: const EdgeInsets.all(12),
                      itemCount: _rows.length,
                      separatorBuilder: (_, __) => const Divider(height: 1),
                      itemBuilder: (context, index) {
                        final row = _rows[index];
                        final id = row['id'] as String? ?? '';
                        final preview = JsonEncoder.withIndent('  ').convert(row);
                        return ListTile(
                          title: Text(id.isEmpty ? l10n.cabinetRowFallback('${index + 1}') : id),
                          subtitle: Text(
                            preview,
                            maxLines: 4,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                          isThreeLine: true,
                          onTap: () => _editRow(existing: row),
                          trailing: IconButton(
                            icon: const Icon(Icons.delete_outline),
                            onPressed: () => _deleteRow(row),
                          ),
                        );
                      },
                    ),
        ),
      ],
    );
  }
}
