import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Org cabinets list — metadata only, read-mostly (L04).
class CompanyCabinetsPage extends StatefulWidget {
  const CompanyCabinetsPage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyCabinetsPage> createState() => _CompanyCabinetsPageState();
}

class _CompanyCabinetsPageState extends State<CompanyCabinetsPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _cabinets = const [];

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
      final items = await companyContext.api.listOrgCabinets(widget.companyId);
      if (!mounted) return;
      setState(() {
        _cabinets = items;
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

  @override
  Widget build(BuildContext context) {
    final rows = _cabinets
        .map(
          (c) => AppEntityRow(
            id: c['id'] as String,
            title: c['name'] as String? ?? c['id'] as String,
            subtitle: c['owner_email'] as String?,
            cells: {
              'owner': c['owner_email'] as String? ?? '—',
              'status': c['status'] as String? ?? '—',
            },
          ),
        )
        .toList();

    return Column(
      children: [
        if (_error != null) InlineErrorBanner(message: _error!),
        Expanded(
          child: AppEntityCollection(
            loading: _loading,
            rows: rows,
            columns: const [
              AppEntityColumn(id: 'name', label: 'Cabinet'),
              AppEntityColumn(id: 'owner', label: 'Owner'),
              AppEntityColumn(id: 'status', label: 'Status'),
            ],
            onOpen: (row) {
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(content: Text('${row.title} — read-only org view')),
              );
            },
            empty: const EmptyState(
              title: 'No cabinets',
              subtitle: 'Employees create cabinets in Employee contour',
            ),
          ),
        ),
      ],
    );
  }
}
