import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Meta tables browser — read-only rows preview (L05/L06 interpreter).
class CabinetTablesTabPage extends StatefulWidget {
  const CabinetTablesTabPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetTablesTabPage> createState() => _CabinetTablesTabPageState();
}

class _CabinetTablesTabPageState extends State<CabinetTablesTabPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _tables = const [];
  String? _selectedSlug;
  List<Map<String, dynamic>> _rows = const [];

  @override
  void initState() {
    super.initState();
    _loadTables();
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
      final result = await workContext.api.queryCabinetRows(
        cabinetId: widget.cabinetId,
        tableSlug: slug,
      );
      if (!mounted) return;
      final rows = result['rows'];
      setState(() {
        _rows = rows is List ? rows.cast<Map<String, dynamic>>() : const [];
      });
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
        Expanded(
          child: _selectedSlug == null
              ? const Center(child: Text('Select a table to preview rows'))
              : _rows.isEmpty
                  ? const Center(child: Text('No rows'))
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
                        );
                      },
                    ),
        ),
      ],
    );
  }
}
