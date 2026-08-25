import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
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
    this.primaryColumnLabel,
  });

  final List<AppEntityRow> rows;
  final List<AppEntityColumn> columns;
  final ValueChanged<AppEntityRow> onOpen;
  final List<Widget>? toolbar;
  final Widget? empty;
  final bool loading;
  final bool allowModeToggle;
  final AppEntityCollectionMode? initialMode;
  final String? primaryColumnLabel;

  static const double _columnSpacing = 12;
  static const double _horizontalMargin = 12;
  static const double _primaryMinWidth = 140;

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
          EmptyPlaceholder(title: l10n.commonEmpty);
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
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxTableWidth = AppBreakpoints.contentMaxWidth;
        final parentWidth = constraints.maxWidth.isFinite
            ? constraints.maxWidth
            : maxTableWidth;
        final tableWidth = parentWidth.clamp(0.0, maxTableWidth).toDouble();
        final primaryLabel =
            widget.primaryColumnLabel ?? l10n.commonEntity;
        final fixedWidth = widget.columns.fold<double>(
          0,
          (sum, c) => sum + (c.width ?? 0),
        );
        final minTableWidth = AppEntityCollection._horizontalMargin * 2 +
            AppEntityCollection._primaryMinWidth +
            fixedWidth +
            widget.columns.length * AppEntityCollection._columnSpacing;
        final needsScroll = minTableWidth > tableWidth;

        final table = DataTable(
          showCheckboxColumn: false,
          columnSpacing: AppEntityCollection._columnSpacing,
          horizontalMargin: AppEntityCollection._horizontalMargin,
          dataRowMinHeight: 40,
          headingRowHeight: 44,
          columns: [
            DataColumn(label: Text(primaryLabel)),
            ...widget.columns.map((c) => DataColumn(label: Text(c.label))),
          ],
          rows: [
            for (final row in widget.rows)
              DataRow(
                onSelectChanged: (_) => widget.onOpen(row),
                cells: [
                  DataCell(
                    Text(row.title, overflow: TextOverflow.ellipsis),
                  ),
                  ...widget.columns.map((c) => _dataCell(row.cells[c.id] ?? '', c)),
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
