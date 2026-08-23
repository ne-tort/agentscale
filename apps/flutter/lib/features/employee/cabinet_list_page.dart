import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/cabinet_import_bundle_page.dart';
import 'package:prodavan/features/employee/dynamic_cabinet_shell.dart';

/// Employee cabinet home — owned cabinets only (L05).
class CabinetListPage extends StatefulWidget {
  const CabinetListPage({super.key});

  @override
  State<CabinetListPage> createState() => _CabinetListPageState();
}

class _CabinetListPageState extends State<CabinetListPage> {
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
      final items = await workContext.api.listCabinets();
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

  Future<void> _createCabinet() async {
    final companyId = workContext.companyId;
    if (companyId == null) {
      setState(() => _error = 'No company_id from /me memberships');
      return;
    }
    final nameCtrl = TextEditingController(text: 'My cabinet');
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Create cabinet'),
        content: TextField(controller: nameCtrl, decoration: const InputDecoration(labelText: 'Name')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Create')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await workContext.api.createCabinet(name: nameCtrl.text.trim(), companyId: companyId);
      await _reload();
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  void _openImportBundle() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const CabinetImportBundlePage()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rows = _cabinets
        .map(
          (c) => AppEntityRow(
            id: c['id'] as String,
            title: c['name'] as String? ?? c['id'] as String,
            subtitle: c['status'] as String?,
          ),
        )
        .toList();

    return AppScaffold(
      title: const Text('Cabinets'),
      actions: [
        IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
        IconButton(onPressed: _openImportBundle, icon: const Icon(Icons.upload_file), tooltip: 'Import bundle'),
        IconButton(onPressed: _createCabinet, icon: const Icon(Icons.add)),
      ],
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              columns: const [
                AppEntityColumn(id: 'name', label: 'Name'),
                AppEntityColumn(id: 'status', label: 'Status'),
              ],
              onOpen: (row) {
                workContext.enterCabinet(row.id);
                Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => DynamicCabinetShell(cabinetId: row.id, cabinetName: row.title),
                  ),
                );
              },
              empty: EmptyState(
                title: 'No cabinets',
                subtitle: 'Create a Base cabinet to start',
                action: AppButton(label: 'Create', expanded: false, onPressed: _createCabinet),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
