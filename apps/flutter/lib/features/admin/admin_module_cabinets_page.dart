import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin module cabinets table — checkbox toggles MC bind via updateModule.
class AdminModuleCabinetsPage extends StatefulWidget {
  const AdminModuleCabinetsPage({
    super.key,
    required this.moduleId,
    required this.moduleName,
  });

  final String moduleId;
  final String moduleName;

  @override
  State<AdminModuleCabinetsPage> createState() => _AdminModuleCabinetsPageState();
}

class _AdminModuleCabinetsPageState extends State<AdminModuleCabinetsPage> {
  bool _loading = true;
  List<Map<String, dynamic>> _items = const [];
  Set<String> _boundIds = {};

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() => _loading = true);
    try {
      final items = await adminContext.api.listModuleCabinets(widget.moduleId);
      if (!mounted) return;
      setState(() {
        _items = items;
        _boundIds = {
          for (final item in items)
            if (item['bound'] == true) (item['cabinet_id'] as String? ?? ''),
        }..removeWhere((id) => id.isEmpty);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  bool _enabledOf(AppEntityRow row) => _boundIds.contains(row.id);

  Future<void> _onEnabledChanged(AppEntityRow row, bool enabled) async {
    final next = Set<String>.from(_boundIds);
    if (enabled) {
      next.add(row.id);
    } else {
      next.remove(row.id);
    }
    final previous = Set<String>.from(_boundIds);
    setState(() => _boundIds = next);
    try {
      final updated = await adminContext.api.updateModule(
        moduleId: widget.moduleId,
        cabinetIds: next.toList(),
      );
      if (!mounted) return;
      final ids = updated['cabinet_ids'];
      setState(() {
        _boundIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : next;
      });
      await _reload();
    } catch (e) {
      if (!mounted) return;
      setState(() => _boundIds = previous);
      AppErrors.showSnack(context, e);
    }
  }

  String _cell(dynamic v, AppLocalizations l10n) {
    if (v == null) return l10n.commonEmDash;
    final s = '$v'.trim();
    return s.isEmpty ? l10n.commonEmDash : s;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _items.map((item) {
      final id = item['cabinet_id'] as String? ?? '';
      return AppEntityRow(
        id: id,
        title: item['cabinet_name'] as String? ?? id,
        cells: {
          'company': _cell(item['company_name'], l10n),
          'id': _cell(id, l10n),
          'modules': _cell(item['modules_count'], l10n),
        },
      );
    }).toList();

    return AppScaffold(
      title: Text(l10n.commonCabinets),
      body: AppEntityCollection(
        loading: _loading,
        rows: rows,
        primaryColumnLabel: l10n.commonName,
        columns: [
          AppEntityColumn(id: 'company', label: l10n.commonCompany, flex: 2),
          AppEntityColumn(id: 'id', label: l10n.commonId, flex: 2),
          AppEntityColumn(id: 'modules', label: l10n.navModules, width: 90),
        ],
        onOpen: (_) {},
        enabledOf: _enabledOf,
        onEnabledChanged: _onEnabledChanged,
        empty: EmptyPlaceholder(
          title: l10n.commonNothingFound,
          icon: Icons.inventory_2_outlined,
        ),
      ),
    );
  }
}
