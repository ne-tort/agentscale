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

enum AppEntityColumnAlign { start, center, end }

/// Column alignment in [AppEntityCollection] table mode:
/// - [start] — text labels (name, email, status)
/// - [center] — short metrics and counts (default for numeric columns)
/// - [end] — legacy; prefer [center] for metrics

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
    this.rowColor,
    this.titleBold = false,
  });

  final String id;
  final String title;
  final String? subtitle;
  final Map<String, String> cells;
  final Map<String, Widget> cellWidgets;
  final Widget? leading;
  final Widget? trailing;

  /// Optional primary-title color (legacy; prefer [rowColor]).
  final Color? titleColor;

  /// Color for all cells in the row (title + columns).
  final Color? rowColor;

  /// Bold font on the primary (first) column.
  final bool titleBold;

  Color? get effectiveColor => rowColor ?? titleColor;
}

/// Custom icon action shown in edit mode (long-press) for a row.
class AppEntityRowAction {
  const AppEntityRowAction({
    required this.tooltip,
    required this.onPressed,
    this.icon,
    this.iconBuilder,
    this.visible,
  });

  final IconData? icon;
  final Widget Function(BuildContext context, AppEntityRow row)? iconBuilder;
  final String tooltip;
  final Future<void> Function(AppEntityRow row) onPressed;
  final bool Function(AppEntityRow row)? visible;
}

/// Unified entity collection — table on wide, list on narrow (canon 07).
///
/// Long-press enters edit mode when [onCopy] / [onDelete] / [onEnabledChanged]
/// or [rowActions] are set (inline icons in the last column / list trailing).
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
    this.copyableOf,
    this.deletableOf,
    this.enabledOf,
    this.onEnabledChanged,
    this.rowActions = const [],
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

  /// When set, long-press shows copy only for rows where this returns true.
  final bool Function(AppEntityRow row)? copyableOf;

  /// When set, long-press shows delete only for rows where this returns true.
  final bool Function(AppEntityRow row)? deletableOf;

  /// When set with [onEnabledChanged], long-press shows a trailing switch.
  final bool Function(AppEntityRow row)? enabledOf;
  final Future<void> Function(AppEntityRow row, bool enabled)? onEnabledChanged;

  /// Custom row actions (preview, download, etc.) shown inline on long-press.
  final List<AppEntityRowAction> rowActions;

  @override
  State<AppEntityCollection> createState() => _AppEntityCollectionState();
}

class _AppEntityCollectionState extends State<AppEntityCollection> {
  static const double _columnSpacing = 12;
  static const double _horizontalMargin = 12;
  static const double _primaryMinWidth = 140;
  static const double _flexColumnMinWidth = 96;
  static const double _mutateTrailingMinWidth = 128;

  String? _editFocusId;

  bool get _mutateEnabled =>
      widget.onCopy != null ||
      widget.onDelete != null ||
      widget.onEnabledChanged != null ||
      widget.rowActions.isNotEmpty;

  bool _canCopy(AppEntityRow row) {
    if (widget.onCopy == null) return false;
    return widget.copyableOf?.call(row) ?? true;
  }

  bool _canDelete(AppEntityRow row) {
    if (widget.onDelete == null) return false;
    return widget.deletableOf?.call(row) ?? true;
  }

  bool _rowActionVisible(AppEntityRowAction action, AppEntityRow row) =>
      action.visible?.call(row) ?? true;

  bool _rowHasRowActions(AppEntityRow row) {
    for (final action in widget.rowActions) {
      if (_rowActionVisible(action, row)) return true;
    }
    return false;
  }

  bool _rowHasMutateActions(AppEntityRow row) =>
      _canCopy(row) ||
      _canDelete(row) ||
      (widget.enabledOf != null && widget.onEnabledChanged != null) ||
      _rowHasRowActions(row);

  AppEntityCollectionMode _effectiveMode(BuildContext context) {
    if (widget.mode != null) return widget.mode!;
    return AppBreakpoints.isWide(context)
        ? AppEntityCollectionMode.table
        : AppEntityCollectionMode.list;
  }

  Alignment _alignment(AppEntityColumnAlign align) => switch (align) {
        AppEntityColumnAlign.end => Alignment.centerRight,
        AppEntityColumnAlign.center => Alignment.center,
        AppEntityColumnAlign.start => Alignment.centerLeft,
      };

  TextAlign _textAlign(AppEntityColumnAlign align) => switch (align) {
        AppEntityColumnAlign.end => TextAlign.right,
        AppEntityColumnAlign.center => TextAlign.center,
        AppEntityColumnAlign.start => TextAlign.left,
      };

  TextStyle? _titleStyle(AppEntityRow row, TextStyle? base) {
    final color = row.effectiveColor;
    final weight = row.titleBold ? FontWeight.w600 : null;
    if (color == null && weight == null) return base;
    return (base ?? const TextStyle()).copyWith(color: color, fontWeight: weight);
  }

  TextStyle? _cellTextStyle(AppEntityRow row, TextStyle? base) {
    final color = row.effectiveColor;
    if (color == null) return base;
    return (base ?? const TextStyle()).copyWith(color: color);
  }

  void _clearEdit() {
    if (_editFocusId == null) return;
    setState(() => _editFocusId = null);
  }

  void _enterEdit(AppEntityRow row) {
    if (!_mutateEnabled || !_rowHasMutateActions(row)) return;
    setState(() => _editFocusId = row.id);
  }

  Widget _mutateTrailing(BuildContext context, AppEntityRow row) {
    final l10n = AppLocalizations.of(context);
    final onSurface = context.appColors.onSurface;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (_canCopy(row))
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
        if (_canDelete(row))
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
        for (final action in widget.rowActions)
          if (_rowActionVisible(action, row))
            IconButton(
              tooltip: action.tooltip,
              icon: action.iconBuilder?.call(context, row) ??
                  Icon(action.icon, size: 20, color: onSurface),
              padding: EdgeInsets.zero,
              visualDensity: VisualDensity.compact,
              constraints: const BoxConstraints(
                minWidth: AppInsets.trailingIconExtent,
                minHeight: AppInsets.trailingIconExtent,
              ),
              onPressed: () async {
                await action.onPressed(row);
                if (mounted) _clearEdit();
              },
            ),
      ],
    );
  }

  Widget _primaryCellContent(AppEntityRow row, TextStyle? bodyMedium) {
    final title = Text(
      row.title,
      overflow: TextOverflow.ellipsis,
      style: _titleStyle(row, bodyMedium),
    );
    if (row.leading == null) return title;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        row.leading!,
        const SizedBox(width: AppSpacing.sm),
        Flexible(child: title),
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
      final bodyMedium = Theme.of(context).textTheme.bodyMedium;
      return ListView.builder(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        itemCount: widget.rows.length,
        itemBuilder: (context, i) {
          final row = widget.rows[i];
          final editing = _editFocusId == row.id;
          final subtitleStyle = row.effectiveColor != null
              ? bodyMedium?.copyWith(color: row.effectiveColor)
              : bodyMedium;
          return Padding(
            padding: EdgeInsets.only(
              bottom: i == widget.rows.length - 1 ? 0 : AppSpacing.sm,
            ),
            child: AppListItem(
              title: Text(
                row.title,
                style: _titleStyle(row, bodyMedium),
              ),
              subtitle:
                  row.subtitle != null ? Text(row.subtitle!, style: subtitleStyle) : null,
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
              onLongPress: _mutateEnabled && _rowHasMutateActions(row)
                  ? () {
                      if (editing) {
                        _clearEdit();
                      } else {
                        _enterEdit(row);
                      }
                    }
                  : null,
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
    final bodyMedium = Theme.of(context).textTheme.bodyMedium;

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
        final flexMin = widget.columns
                .where((c) => c.width == null)
                .length *
            _flexColumnMinWidth;
        final mutateMin = _mutateEnabled ? _mutateTrailingMinWidth : 0;
        final minTableWidth = _horizontalMargin * 2 +
            _primaryMinWidth +
            fixedWidth +
            flexMin +
            mutateMin +
            widget.columns.length * _columnSpacing;
        final needsScroll = minTableWidth > tableWidth;
        final showActionsCol = _mutateEnabled;

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
                            overflow: TextOverflow.ellipsis,
                          ),
                        )
                      : const SizedBox.shrink(),
                  numeric: c.align == AppEntityColumnAlign.end,
                ),
              ),
              if (showActionsCol)
                const DataColumn(label: SizedBox.shrink()),
            ],
            rows: [
              for (final row in widget.rows)
                DataRow(
                  selected: _editFocusId == row.id,
                  onSelectChanged: (_) {
                    // While editing, ignore select so action IconButtons receive
                    // the tap (DataRow otherwise steals it and clears edit).
                    if (_editFocusId == row.id) return;
                    widget.onOpen(row);
                  },
                  onLongPress: _mutateEnabled && _rowHasMutateActions(row)
                      ? () {
                          if (_editFocusId == row.id) {
                            _clearEdit();
                          } else {
                            _enterEdit(row);
                          }
                        }
                      : null,
                  cells: [
                    DataCell(
                      Align(
                        alignment: Alignment.centerLeft,
                        child: _primaryCellContent(row, bodyMedium),
                      ),
                    ),
                    for (final col in widget.columns)
                      _dataCell(context, row, col),
                    if (showActionsCol)
                      DataCell(
                        _editFocusId == row.id
                            ? Align(
                                alignment: Alignment.centerRight,
                                child: _mutateTrailing(context, row),
                              )
                            : const SizedBox.shrink(),
                        // Override row select so delete/copy taps are not stolen.
                        onTap: () {},
                      ),
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

        Widget tableBody = child;
        if (needsScroll) {
          tableBody = SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: child,
          );
        }
        return SingleChildScrollView(
          scrollDirection: Axis.vertical,
          child: tableBody,
        );
      },
    );
  }

  DataCell _dataCell(
    BuildContext context,
    AppEntityRow row,
    AppEntityColumn column,
  ) {
    final alignment = _alignment(column.align);
    final bodyMedium = Theme.of(context).textTheme.bodyMedium;
    final widgetCell = row.cellWidgets[column.id];
    final child = widgetCell ??
        Text(
          row.cells[column.id] ?? '',
          overflow: TextOverflow.ellipsis,
          maxLines: 1,
          textAlign: _textAlign(column.align),
          style: _cellTextStyle(row, bodyMedium),
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
