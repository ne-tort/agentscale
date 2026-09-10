import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_switch.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Selector: remote SQL databases; trailing [AppSwitch] single-select.
class RemoteDatabasePickerPage extends StatefulWidget {
  const RemoteDatabasePickerPage({
    super.key,
    required this.api,
    required this.cabinetId,
    required this.moduleId,
    required this.listActionId,
    required this.rowId,
    this.selectedDatabase,
  });

  final ProdavanApi api;
  final String cabinetId;
  final String moduleId;
  final String listActionId;
  final String rowId;
  final String? selectedDatabase;

  @override
  State<RemoteDatabasePickerPage> createState() =>
      _RemoteDatabasePickerPageState();
}

class _RemoteDatabasePickerPageState extends State<RemoteDatabasePickerPage> {
  bool _loading = true;
  Object? _error;
  List<String> _databases = const [];

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
      final raw = result['databases'];
      final names = <String>[];
      if (raw is List) {
        for (final item in raw) {
          if (item is! Map) continue;
          final name = item['name']?.toString() ?? '';
          if (name.isEmpty) continue;
          names.add(name);
        }
      }
      if (!mounted) return;
      setState(() {
        _databases = names;
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

  void _select(String name) {
    Navigator.of(context).pop(name);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final title = locale.languageCode == 'en' ? 'Database' : 'База';
    final nameLabel = locale.languageCode == 'en' ? 'Name' : 'Название';

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
              : _databases.isEmpty
                  ? EmptyPlaceholder(
                      icon: Icons.storage_outlined,
                      title: locale.languageCode == 'en'
                          ? 'No databases'
                          : 'Нет баз',
                      fillViewport: false,
                    )
                  : Padding(
                      padding:
                          const EdgeInsets.symmetric(vertical: AppSpacing.sm),
                      child: AppEntityCollection(
                        primaryColumnLabel: nameLabel,
                        columns: const [],
                        rows: [
                          for (final name in _databases)
                            AppEntityRow(
                              id: name,
                              title: name,
                              trailing: AppSwitch(
                                value: widget.selectedDatabase == name,
                                onChanged: (v) {
                                  if (v) _select(name);
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
