import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_switch.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Selector: remote SQL tables with Name + Rows; trailing [AppSwitch] single-select.
class RemoteTablePickerPage extends StatefulWidget {
  const RemoteTablePickerPage({
    super.key,
    required this.api,
    required this.cabinetId,
    required this.moduleId,
    required this.listActionId,
    required this.rowId,
    this.selectedTable,
  });

  final ProdavanApi api;
  final String cabinetId;
  final String moduleId;
  final String listActionId;
  final String rowId;
  final String? selectedTable;

  @override
  State<RemoteTablePickerPage> createState() => _RemoteTablePickerPageState();
}

class _RemoteTablePickerPageState extends State<RemoteTablePickerPage> {
  bool _loading = true;
  Object? _error;
  List<_RemoteTableRow> _tables = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await widget.api.invokeModuleAction(
        cabinetId: widget.cabinetId,
        moduleId: widget.moduleId,
        actionId: widget.listActionId,
        rowId: widget.rowId,
      );
      final raw = result['tables'];
      final tables = <_RemoteTableRow>[];
      if (raw is List) {
        for (final item in raw) {
          if (item is! Map) continue;
          final name = item['name']?.toString() ?? '';
          if (name.isEmpty) continue;
          final count = item['row_count'];
          tables.add(
            _RemoteTableRow(
              name: name,
              rowCount: count is num
                  ? count.toInt()
                  : int.tryParse(count?.toString() ?? '') ?? 0,
            ),
          );
        }
      }
      if (!mounted) return;
      setState(() {
        _tables = tables;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      AppErrors.showSnack(context, e);
      final body = e is ProdavanApiException ? e.body : '';
      if (body.contains('REMOTE_AUTH_FAILED')) {
        Navigator.of(context).pop();
        return;
      }
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  void _select(String name) {
    Navigator.of(context).pop(name);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final title = locale.languageCode == 'en' ? 'Table' : 'Таблица';
    final nameLabel = locale.languageCode == 'en' ? 'Name' : 'Название';
    final rowsLabel = locale.languageCode == 'en' ? 'Rows' : 'Строк';

    return AppScaffold(
      title: Text(title),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? EmptyPlaceholder(
                  icon: Icons.error_outline,
                  title: AppErrors.localize(context, _error!),
                  fillViewport: false,
                  action: TextButton(
                    onPressed: _load,
                    child: Text(l10n.commonReload),
                  ),
                )
              : _tables.isEmpty
                  ? EmptyPlaceholder(
                      icon: Icons.table_chart_outlined,
                      title: locale.languageCode == 'en'
                          ? 'No tables'
                          : 'Нет таблиц',
                      fillViewport: false,
                    )
                  : Padding(
                      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
                      child: AppEntityCollection(
                        primaryColumnLabel: nameLabel,
                        columns: [
                          AppEntityColumn(id: 'rows', label: rowsLabel),
                        ],
                        rows: [
                          for (final t in _tables)
                            AppEntityRow(
                              id: t.name,
                              title: t.name,
                              cells: {'rows': '${t.rowCount}'},
                              trailing: AppSwitch(
                                value: widget.selectedTable == t.name,
                                onChanged: (v) {
                                  if (v) _select(t.name);
                                },
                              ),
                            ),
                        ],
                        onOpen: (row) => _select(row.id),
                      ),
                    ),
    );
  }
}

class _RemoteTableRow {
  const _RemoteTableRow({required this.name, required this.rowCount});
  final String name;
  final int rowCount;
}
