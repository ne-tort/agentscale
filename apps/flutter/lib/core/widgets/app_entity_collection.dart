import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/l10n/app_localizations.dart';

enum AppEntityCollectionMode { list, table }

class AppEntityColumn {
  const AppEntityColumn({
    required this.id,
    required this.label,
    this.flex = 1,
  });

  final String id;
  final String label;
  final int flex;
}

class AppEntityRow {
  const AppEntityRow({
    required this.id,
    required this.title,
    this.subtitle,
    this.cells = const {},
    this.leading,
    this.trailing,
  });

  final String id;
  final String title;
  final String? subtitle;
  final Map<String, String> cells;
  final Widget? leading;
  final Widget? trailing;
}

/// Unified list/table surface — primary entity management chrome (canon 07).
class AppEntityCollection extends StatefulWidget {
  const AppEntityCollection({
    super.key,
    required this.rows,
    required this.columns,
    required this.onOpen,
    this.toolbar,
    this.empty,
    this.loading = false,
    this.allowModeToggle = true,
    this.initialMode,
  });

  final List<AppEntityRow> rows;
  final List<AppEntityColumn> columns;
  final ValueChanged<AppEntityRow> onOpen;
  final List<Widget>? toolbar;
  final Widget? empty;
  final bool loading;
  final bool allowModeToggle;
  final AppEntityCollectionMode? initialMode;

  @override
  State<AppEntityCollection> createState() => _AppEntityCollectionState();
}

class _AppEntityCollectionState extends State<AppEntityCollection> {
  AppEntityCollectionMode? _override;

  AppEntityCollectionMode _effectiveMode(BuildContext context) {
    if (_override != null) return _override!;
    if (widget.initialMode != null) return widget.initialMode!;
    return AppBreakpoints.isWide(context)
        ? AppEntityCollectionMode.table
        : AppEntityCollectionMode.list;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final mode = _effectiveMode(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (widget.toolbar != null || widget.allowModeToggle)
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.sm,
              vertical: AppSpacing.xs,
            ),
            child: Row(
              children: [
                ...?widget.toolbar,
                const Spacer(),
                if (widget.allowModeToggle) ...[
                  AppIconToggle(
                    icon: Icons.view_list_outlined,
                    tooltip: l10n.commonList,
                    selected: mode == AppEntityCollectionMode.list,
                    onPressed: () => setState(
                      () => _override = AppEntityCollectionMode.list,
                    ),
                  ),
                  AppIconToggle(
                    icon: Icons.table_rows_outlined,
                    tooltip: l10n.commonTable,
                    selected: mode == AppEntityCollectionMode.table,
                    onPressed: () => setState(
                      () => _override = AppEntityCollectionMode.table,
                    ),
                  ),
                ],
              ],
            ),
          ),
        Expanded(child: _body(context, mode)),
      ],
    );
  }

  Widget _body(BuildContext context, AppEntityCollectionMode mode) {
    final l10n = AppLocalizations.of(context);
    if (widget.loading) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (widget.rows.isEmpty) {
      return widget.empty ??
          EmptyState(title: l10n.commonEmpty);
    }
    if (mode == AppEntityCollectionMode.list) {
      return ListView.separated(
        padding: const EdgeInsets.all(AppSpacing.sm),
        itemCount: widget.rows.length,
        separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
        itemBuilder: (context, i) {
          final row = widget.rows[i];
          return AppListItem(
            title: Text(row.title),
            subtitle: row.subtitle != null ? Text(row.subtitle!) : null,
            leading: row.leading,
            trailing: row.trailing ?? const Icon(Icons.chevron_right),
            onTap: () => widget.onOpen(row),
          );
        },
      );
    }
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          minWidth: MediaQuery.sizeOf(context).width,
        ),
        child: DataTable(
          showCheckboxColumn: false,
          columns: [
            DataColumn(label: Text(l10n.commonTitle)),
            ...widget.columns.map((c) => DataColumn(label: Text(c.label))),
          ],
          rows: [
            for (final row in widget.rows)
              DataRow(
                onSelectChanged: (_) => widget.onOpen(row),
                cells: [
                  DataCell(Text(row.title)),
                  ...widget.columns.map(
                    (c) => DataCell(Text(row.cells[c.id] ?? '')),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
