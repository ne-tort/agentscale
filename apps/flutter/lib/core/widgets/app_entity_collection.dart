import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_insets.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_switch.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
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
    this.cellWidgets = const {},
    this.leading,
    this.trailing,
    this.titleColor,
  });

  final String id;
  final String title;
  final String? subtitle;
  final Map<String, String> cells;
  final Map<String, Widget> cellWidgets;
  final Widget? leading;
  final Widget? trailing;
  /// Optional primary-title color (e.g. warning for suspended AI keys).
  final Color? titleColor;
}

/// Unified entity collection — table on wide, list on narrow (canon 07).
///
/// Long-press enters mutate mode when [onCopy] / [onDelete] / [onEnabledChanged]
/// are set (copy + delete icons; optional enable switch as rightmost).
class AppEntityCollection extends StatefulWidget {
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
    this.showHeader = true,
    this.onCopy,
    this.onDelete,
    this.enabledOf,
    this.onEnabledChanged,
  });

  final List<AppEntityRow> rows;
  final List<AppEntityColumn> columns;
  final ValueChanged<AppEntityRow> onOpen;
  final List<Widget>? toolbar;
  final Widget? empty;
  final bool loading;
  final AppEntityCollectionMode? mode;
  final String? primaryColumnLabel;

  /// When false (table mode), hides the heading row entirely.
  final bool showHeader;

  final Future<void> Function(AppEntityRow row)? onCopy;
  final Future<void> Function(AppEntityRow row)? onDelete;

  /// When set with [onEnabledChanged], long-press shows a trailing switch.
  final bool Function(AppEntityRow row)? enabledOf;
  final Future<void> Function(AppEntityRow row, bool enabled)? onEnabledChanged;

  @override
  State<AppEntityCollection> createState() => _AppEntityCollectionState();
}

class _AppEntityCollectionState extends State<AppEntityCollection> {
  static const double _columnSpacing = 12;
  static const double _horizontalMargin = 12;
  static const double _primaryMinWidth = 140;

  String? _editFocusId;

  bool get _mutateEnabled =>
      widget.onCopy != null ||
      widget.onDelete != null ||
      widget.onEnabledChanged != null;

  AppEntityCollectionMode _effectiveMode(BuildContext context) {
    if (widget.mode != null) return widget.mode!;
    return AppBreakpoints.isWide(context)
        ? AppEntityCollectionMode.table
        : AppEntityCollectionMode.list;
  }

  Alignment _alignment(AppEntityColumnAlign align) =>
      align == AppEntityColumnAlign.end
          ? Alignment.centerRight
          : Alignment.centerLeft;

  TextAlign _textAlign(AppEntityColumnAlign align) =>
      align == AppEntityColumnAlign.end ? TextAlign.right : TextAlign.left;

  void _clearEdit() {
    if (_editFocusId == null) return;
    setState(() => _editFocusId = null);
  }

  void _enterEdit(AppEntityRow row) {
    if (!_mutateEnabled) return;
    setState(() => _editFocusId = row.id);
  }

  Widget _mutateTrailing(BuildContext context, AppEntityRow row) {
    final l10n = AppLocalizations.of(context);
    final onSurface = context.appColors.onSurface;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (widget.onCopy != null)
          IconButton(
            tooltip: l10n.commonCopy,
            icon: Icon(Icons.copy_outlined, size: 20, color: onSurface),
            padding: EdgeInsets.zero,
            visualDensity: VisualDensity.compact,
            constraints: const BoxConstraints(
              minWidth: AppInsets.trailingIconExtent,
              minHeight: AppInsets.trailingIconExtent,
            ),
            onPressed: () async {
              await widget.onCopy!(row);
              if (mounted) _clearEdit();
            },
          ),
        if (widget.onDelete != null)
          IconButton(
            tooltip: l10n.commonDelete,
            icon: Icon(Icons.delete_outline, size: 20, color: onSurface),
            padding: EdgeInsets.zero,
            visualDensity: VisualDensity.compact,
            constraints: const BoxConstraints(
              minWidth: AppInsets.trailingIconExtent,
              minHeight: AppInsets.trailingIconExtent,
            ),
            onPressed: () async {
              await widget.onDelete!(row);
              if (mounted) _clearEdit();
            },
          ),
        if (widget.enabledOf != null && widget.onEnabledChanged != null)
          AppSwitch(
            value: widget.enabledOf!(row),
            onChanged: (v) async {
              await widget.onEnabledChanged!(row, v);
              if (mounted) _clearEdit();
            },
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final effective = _effectiveMode(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (widget.toolbar != null)
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.xs,
            ),
            child: Row(children: [...?widget.toolbar, const Spacer()]),
          ),
        Expanded(child: _body(context, effective)),
      ],
    );
  }

  Widget _body(BuildContext context, AppEntityCollectionMode mode) {
    final l10n = AppLocalizations.of(context);
    if (widget.loading) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (widget.rows.isEmpty) {
      return widget.empty ?? EmptyPlaceholder(title: l10n.commonEmpty);
    }
    if (mode == AppEntityCollectionMode.list) {
      return ListView.builder(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        itemCount: widget.rows.length,
        itemBuilder: (context, i) {
          final row = widget.rows[i];
          final editing = _editFocusId == row.id;
          return Padding(
            padding: EdgeInsets.only(
              bottom: i == widget.rows.length - 1 ? 0 : AppSpacing.sm,
            ),
            child: AppListItem(
              title: Text(
                row.title,
                style: row.titleColor != null
                    ? TextStyle(color: row.titleColor)
                    : null,
              ),
              subtitle: row.subtitle != null ? Text(row.subtitle!) : null,
              leading: row.leading,
              selected: editing,
              trailing: editing
                  ? _mutateTrailing(context, row)
                  : (row.trailing ?? const AppTrailingChevron()),
              onTap: () {
                if (editing) {
                  _clearEdit();
                  return;
                }
                widget.onOpen(row);
              },
              onLongPress:
                  _mutateEnabled ? () => _enterEdit(row) : null,
            ),
          );
        },
      );
    }
    final colors = context.appColors;
    final headingStyle = Theme.of(context).textTheme.labelLarge?.copyWith(
          color: colors.muted,
          fontWeight: FontWeight.w600,
        );

    return LayoutBuilder(
      builder: (context, constraints) {
        final maxTableWidth = AppBreakpoints.contentMaxWidth;
        final parentWidth = constraints.maxWidth.isFinite
            ? constraints.maxWidth
            : maxTableWidth;
        final tableWidth = parentWidth.clamp(0.0, maxTableWidth).toDouble();
        final primaryLabel = widget.primaryColumnLabel ?? l10n.commonEntity;
        final fixedWidth = widget.columns.fold<double>(
          0,
          (sum, c) => sum + (c.width ?? 0),
        );
        final minTableWidth = _horizontalMargin * 2 +
            _primaryMinWidth +
            fixedWidth +
            widget.columns.length * _columnSpacing;
        final needsScroll = minTableWidth > tableWidth;
        final lastColIndex = widget.columns.isEmpty ? -1 : widget.columns.length - 1;

        final table = Theme(
          data: Theme.of(context).copyWith(
            dividerColor: Colors.transparent,
            dividerTheme: const DividerThemeData(
              color: Colors.transparent,
              thickness: 0,
              space: 0,
            ),
          ),
          child: DataTable(
            showCheckboxColumn: false,
            dividerThickness: 0,
            showBottomBorder: false,
            columnSpacing: _columnSpacing,
            horizontalMargin: _horizontalMargin,
            dataRowMinHeight: 40,
            headingRowHeight: widget.showHeader ? 44 : 0,
            headingRowColor: WidgetStatePropertyAll(colors.surface),
            decoration: const BoxDecoration(),
            border: TableBorder.all(width: 0, color: Colors.transparent),
            columns: [
              DataColumn(
                label: widget.showHeader
                    ? Align(
                        alignment: Alignment.centerLeft,
                        child: Text(primaryLabel, style: headingStyle),
                      )
                    : const SizedBox.shrink(),
              ),
              ...widget.columns.map(
                (c) => DataColumn(
                  label: widget.showHeader
                      ? Align(
                          alignment: _alignment(c.align),
                          child: Text(
                            c.label,
                            style: headingStyle,
                            textAlign: _textAlign(c.align),
                            softWrap: false,
                            overflow: TextOverflow.visible,
                          ),
                        )
                      : const SizedBox.shrink(),
                  numeric: c.align == AppEntityColumnAlign.end,
                ),
              ),
            ],
            rows: [
              for (final row in widget.rows)
                DataRow(
                  selected: _editFocusId == row.id,
                  onSelectChanged: (_) {
                    if (_editFocusId == row.id) {
                      _clearEdit();
                      return;
                    }
                    widget.onOpen(row);
                  },
                  onLongPress:
                      _mutateEnabled ? () => _enterEdit(row) : null,
                  cells: [
                    DataCell(
                      Align(
                        alignment: Alignment.centerLeft,
                        child: Text(
                          row.title,
                          overflow: TextOverflow.ellipsis,
                          style: row.titleColor != null
                              ? TextStyle(color: row.titleColor)
                              : null,
                        ),
                      ),
                    ),
                    ...[
                      for (var i = 0; i < widget.columns.length; i++)
                        _editFocusId == row.id &&
                                _mutateEnabled &&
                                i == lastColIndex
                            ? DataCell(
                                Align(
                                  alignment: Alignment.centerRight,
                                  child: _mutateTrailing(context, row),
                                ),
                              )
                            : _dataCell(row, widget.columns[i]),
                    ],
                  ],
                ),
            ],
          ),
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

  DataCell _dataCell(AppEntityRow row, AppEntityColumn column) {
    final alignment = _alignment(column.align);
    final widgetCell = row.cellWidgets[column.id];
    final child = widgetCell ??
        Text(
          row.cells[column.id] ?? '',
          overflow: TextOverflow.ellipsis,
          maxLines: 1,
          textAlign: _textAlign(column.align),
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
