import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/cabinet_row_edit_page.dart';

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
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _tables = const [];
  String? _selectedSlug;
  List<Map<String, dynamic>> _rows = const [];
  List<Map<String, dynamic>> _columns = const [];

  @override
  void initState() {
    super.initState();
    _loadTables().then((_) {
      final slug = widget.initialTableSlug;
      if (slug != null && slug.isNotEmpty && mounted) {
        _loadRows(slug);
      }
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
        _error = e.toString();
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
      setState(() => _error = e.toString());
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
    final slug = _selectedSlug;
    final rowId = row['id'] as String?;
    if (slug == null || rowId == null) return;

    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete row?'),
        content: Text('Delete row $rowId'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
        ],
      ),
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
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_tables.isEmpty) {
      return const EmptyState(title: 'No meta tables in this cabinet yet.');
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_error != null) InlineErrorBanner(message: _error!),
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
            child: Align(
              alignment: Alignment.centerRight,
              child: TextButton.icon(
                onPressed: () => _editRow(),
                icon: const Icon(Icons.add),
                label: const Text('Add row'),
              ),
            ),
          ),
        Expanded(
          child: _selectedSlug == null
              ? const Center(child: Text('Select a table to preview rows'))
              : _rows.isEmpty
                  ? Center(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Text('No rows'),
                          const SizedBox(height: 8),
                          TextButton.icon(
                            onPressed: () => _editRow(),
                            icon: const Icon(Icons.add),
                            label: const Text('Add row'),
                          ),
                        ],
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
                          title: Text(id.isEmpty ? 'Row ${index + 1}' : id),
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
