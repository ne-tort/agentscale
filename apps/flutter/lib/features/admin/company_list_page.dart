import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/admin/admin_company_create_page.dart';
import 'package:prodavan/features/admin/company_detail_page.dart';

/// Platform Admin company list (L04).
class AdminCompanyListPage extends StatefulWidget {
  const AdminCompanyListPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminCompanyListPage> createState() => _AdminCompanyListPageState();
}

class _AdminCompanyListPageState extends State<AdminCompanyListPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _companies = const [];

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
      final items = await adminContext.api.listCompanies();
      if (!mounted) return;
      setState(() {
        _companies = items;
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

  Future<void> _createCompany() async {
    final created = await Navigator.of(context).push<Map<String, String>>(
      MaterialPageRoute<Map<String, String>>(
        builder: (_) => const AdminCompanyCreatePage(),
      ),
    );
    if (created == null) return;
    await _reload();
    if (!mounted) return;
    final companyId = created['id'];
    final companyName = created['name'];
    if (companyId == null || companyName == null) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminCompanyDetailPage(companyId: companyId, companyName: companyName),
      ),
    );
  }

  void _openCompany(AppEntityRow row) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminCompanyDetailPage(companyId: row.id, companyName: row.title),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rows = _companies.map((c) {
      final quota = c['cabinet_quota'] as Map<String, dynamic>? ?? const {};
      final max = quota['max_cabinets'];
      final active = c['active_cabinets'];
      final subtitle = max != null && active != null ? '$active / $max cabinets' : null;
      return AppEntityRow(
        id: c['id'] as String,
        title: c['name'] as String? ?? c['id'] as String,
        subtitle: subtitle,
        cells: {
          'cabinets': subtitle ?? '—',
        },
      );
    }).toList();

    return AppScaffold(
      title: const Text('Companies'),
      actions: [
        IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
        IconButton(onPressed: _createCompany, icon: const Icon(Icons.add)),
      ],
      body: Column(
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              columns: const [
                AppEntityColumn(id: 'name', label: 'Company'),
                AppEntityColumn(id: 'cabinets', label: 'Cabinets'),
              ],
              onOpen: _openCompany,
              empty: EmptyState(
                title: 'No companies',
                subtitle: 'Create a company and invite company.admin',
                action: TextButton(onPressed: _createCompany, child: const Text('Create company')),
              ),
            ),
          ),
        ],
      ),
    );
  }
}