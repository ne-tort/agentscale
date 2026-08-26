import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

enum AppEntityCollectionMode { list, table }

enum AppEntityColumnAlign { start, end }

class AppEntityColumn {
  const AppEntityColumn({
    required this.id,
    required this.label,
    this.flex = 1,
    this.width,
    this.align = AppEntityColumnAlign.start,
  });

  final String id;
  final String label;
  final int flex;
  final double? width;
  final AppEntityColumnAlign align;
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
///
/// Mode is controlled by the page (typically via [AppCollectionViewModeStore]
/// + AppBar [AppCollectionViewModeButton]). When [mode] is null, falls back to
/// breakpoint (wide → table, narrow → list).
class AppEntityCollection extends StatelessWidget {
  const AppEntityCollection({
    super.key,
    required this.rows,
    required this.columns,
    required this.onOpen,
    this.toolbar,
    this.empty,
    this.loading = false,
    this.mode,
    this.primaryColumnLabel,
  });

  final List<AppEntityRow> rows;
  final List<AppEntityColumn> columns;
  final ValueChanged<AppEntityRow> onOpen;
  final List<Widget>? toolbar;
  final Widget? empty;
  final bool loading;
  final AppEntityCollectionMode? mode;
  final String? primaryColumnLabel;

  static const double _columnSpacing = 12;
  static const double _horizontalMargin = 12;
  static const double _primaryMinWidth = 140;

  AppEntityCollectionMode _effectiveMode(BuildContext context) {
    if (mode != null) return mode!;
    return AppBreakpoints.isWide(context)
        ? AppEntityCollectionMode.table
        : AppEntityCollectionMode.list;
  }

  @override
  Widget build(BuildContext context) {
    final effective = _effectiveMode(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (toolbar != null)
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.xs,
            ),
            child: Row(children: [...?toolbar, const Spacer()]),
          ),
        Expanded(child: _body(context, effective)),
      ],
    );
  }

  Widget _body(BuildContext context, AppEntityCollectionMode mode) {
    final l10n = AppLocalizations.of(context);
    if (loading) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (rows.isEmpty) {
      return empty ?? EmptyPlaceholder(title: l10n.commonEmpty);
    }
    if (mode == AppEntityCollectionMode.list) {
      return ListView.separated(
        padding: const EdgeInsets.all(AppSpacing.sm),
        itemCount: rows.length,
        separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
        itemBuilder: (context, i) {
          final row = rows[i];
          return AppListItem(
            title: Text(row.title),
            subtitle: row.subtitle != null ? Text(row.subtitle!) : null,
            leading: row.leading,
            trailing: row.trailing ?? const Icon(Icons.chevron_right),
            onTap: () => onOpen(row),
          );
        },
      );
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxTableWidth = AppBreakpoints.contentMaxWidth;
        final parentWidth = constraints.maxWidth.isFinite
            ? constraints.maxWidth
            : maxTableWidth;
        final tableWidth = parentWidth.clamp(0.0, maxTableWidth).toDouble();
        final primaryLabel = primaryColumnLabel ?? l10n.commonEntity;
        final fixedWidth = columns.fold<double>(
          0,
          (sum, c) => sum + (c.width ?? 0),
        );
        final minTableWidth = _horizontalMargin * 2 +
            _primaryMinWidth +
            fixedWidth +
            columns.length * _columnSpacing;
        final needsScroll = minTableWidth > tableWidth;

        final table = DataTable(
          showCheckboxColumn: false,
          columnSpacing: _columnSpacing,
          horizontalMargin: _horizontalMargin,
          dataRowMinHeight: 40,
          headingRowHeight: 44,
          columns: [
            DataColumn(label: Text(primaryLabel)),
            ...columns.map((c) => DataColumn(label: Text(c.label))),
          ],
          rows: [
            for (final row in rows)
              DataRow(
                onSelectChanged: (_) => onOpen(row),
                cells: [
                  DataCell(
                    Text(row.title, overflow: TextOverflow.ellipsis),
                  ),
                  ...columns.map((c) => _dataCell(row.cells[c.id] ?? '', c)),
                ],
              ),
          ],
        );

        final child = ConstrainedBox(
          constraints: BoxConstraints(
            minWidth: needsScroll ? minTableWidth : tableWidth,
            maxWidth: needsScroll ? minTableWidth : tableWidth,
          ),
          child: table,
        );

        if (!needsScroll) return child;
        return SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: child,
        );
      },
    );
  }

  DataCell _dataCell(String text, AppEntityColumn column) {
    final alignment = column.align == AppEntityColumnAlign.end
        ? Alignment.centerRight
        : Alignment.centerLeft;
    final child = Text(
      text,
      overflow: TextOverflow.ellipsis,
      maxLines: 1,
      textAlign: column.align == AppEntityColumnAlign.end
          ? TextAlign.right
          : TextAlign.left,
    );
    if (column.width != null) {
      return DataCell(
        SizedBox(
          width: column.width,
          child: Align(alignment: alignment, child: child),
        ),
      );
    }
    return DataCell(Align(alignment: alignment, child: child));
  }
}
