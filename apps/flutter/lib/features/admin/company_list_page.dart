import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/admin/admin_company_create_page.dart';
import 'package:prodavan/features/admin/company_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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

  String _cell(dynamic v, AppLocalizations l10n) {
    if (v == null) return l10n.commonEmDash;
    final s = '$v'.trim();
    return s.isEmpty ? l10n.commonEmDash : s;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _companies.map((c) {
      final running = c['running_cabinets'];
      final quota = c['cabinets_quota'] ?? c['cabinet_quota']?['max_cabinets'];
      final cabinetsCell = (running != null && quota != null)
          ? l10n.adminCompanyCabinetsRunning('$running', '$quota')
          : l10n.commonEmDash;
      final description = _cell(c['description'], l10n);
      return AppEntityRow(
        id: c['id'] as String,
        title: c['name'] as String? ?? c['id'] as String,
        subtitle: description != l10n.commonEmDash ? description : null,
        cells: {
          'description': description,
          'employees': _cell(c['employees_total'], l10n),
          'cabinets': cabinetsCell,
        },
      );
    }).toList();

    return AppScaffold(
      title: Text(l10n.navCompanies),
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
              primaryColumnLabel: l10n.commonCompany,
              columns: [
                AppEntityColumn(id: 'description', label: l10n.commonDescription),
                AppEntityColumn(
                  id: 'employees',
                  label: l10n.commonEmployees,
                  width: 72,
                  align: AppEntityColumnAlign.end,
                ),
                AppEntityColumn(
                  id: 'cabinets',
                  label: l10n.commonCabinets,
                  width: 88,
                  align: AppEntityColumnAlign.end,
                ),
              ],
              onOpen: _openCompany,
              empty: EmptyPlaceholder(
                title: l10n.adminNoCompanies,
                subtitle: l10n.adminCreateCompanyAndInviteAdmin,
                action: TextButton(onPressed: _createCompany, child: Text(l10n.adminCreateCompany)),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
