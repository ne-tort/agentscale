import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_insets.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_radio.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_switch.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Row model for [AppCatalogSelectPage] (static enums or catalog entries).
class AppCatalogSelectItem {
  const AppCatalogSelectItem({
    required this.id,
    required this.title,
    this.subtitle,
    this.icon,
    this.leading,
    this.enabled = true,
    this.payload = const {},
  });

  final String id;
  final String title;
  final String? subtitle;
  final IconData? icon;
  final Widget? leading;
  final bool enabled;
  final Map<String, dynamic> payload;

  Widget? get effectiveLeading =>
      leading ?? (icon != null ? Icon(icon, size: 22) : null);
}

/// Table-like catalog / enum picker — single (radio) or multi (switch).
///
/// When [allowCreate]/[allowEdit]/[allowDelete] are set, long-press enters
/// edit mode for mutate actions. Static enum pickers leave those flags false.
class AppCatalogSelectPage extends StatefulWidget {
  const AppCatalogSelectPage({
    super.key,
    required this.title,
    required this.items,
    this.multiSelect = false,
    this.selectedIds = const {},
    this.searchEnabled = false,
    this.warningBanner,
    this.empty,
    this.onConfirm,
    this.popOnSelect = true,
    this.allowCreate = false,
    this.allowEdit = false,
    this.allowDelete = false,
    this.addFieldTitle,
    this.onCreate,
    this.onEdit,
    this.onDelete,
  });

  final String title;
  final List<AppCatalogSelectItem> items;
  final bool multiSelect;
  final Set<String> selectedIds;
  final bool searchEnabled;
  final String? warningBanner;
  final Widget? empty;
  final ValueChanged<Set<String>>? onConfirm;
  final bool popOnSelect;

  final bool allowCreate;
  final bool allowEdit;
  final bool allowDelete;
  final String? addFieldTitle;
  final Future<void> Function(String name)? onCreate;
  final Future<void> Function(AppCatalogSelectItem item)? onEdit;
  final Future<void> Function(AppCatalogSelectItem item)? onDelete;

  @override
  State<AppCatalogSelectPage> createState() => _AppCatalogSelectPageState();
}

class _AppCatalogSelectPageState extends State<AppCatalogSelectPage> {
  late Set<String> _selected;
  late List<AppCatalogSelectItem> _items;
  String _query = '';
  String? _editFocusId;

  @override
  void initState() {
    super.initState();
    _selected = {...widget.selectedIds};
    _items = List.of(widget.items);
  }

  @override
  void didUpdateWidget(covariant AppCatalogSelectPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.items != widget.items) {
      _items = List.of(widget.items);
    }
    if (oldWidget.selectedIds != widget.selectedIds) {
      _selected = {...widget.selectedIds};
    }
  }

  List<AppCatalogSelectItem> get _filtered {
    final q = _query.trim().toLowerCase();
    if (q.isEmpty) return _items;
    return _items
        .where(
          (i) =>
              i.title.toLowerCase().contains(q) ||
              (i.subtitle?.toLowerCase().contains(q) ?? false),
        )
        .toList();
  }

  bool get _mutateEnabled =>
      widget.allowEdit || widget.allowDelete || widget.allowCreate;

  void _toggle(String id) {
    if (_editFocusId != null) {
      setState(() => _editFocusId = null);
      return;
    }
    setState(() {
      if (widget.multiSelect) {
        if (_selected.contains(id)) {
          _selected.remove(id);
        } else {
          _selected.add(id);
        }
      } else {
        _selected = {id};
      }
    });
    // Apply immediately — no external Done / Save.
    widget.onConfirm?.call({..._selected});
    if (!widget.multiSelect && widget.popOnSelect) {
      Navigator.of(context).pop(_selected);
    }
  }

  Future<void> _create(String name) async {
    final cb = widget.onCreate;
    if (cb == null) return;
    await cb(name);
    // Parent may refresh [items]; keep local copy in sync if it did.
    if (mounted && widget.items != _items) {
      setState(() => _items = List.of(widget.items));
    }
  }

  Future<void> _edit(AppCatalogSelectItem item) async {
    final cb = widget.onEdit;
    if (cb == null) return;
    await cb(item);
    if (mounted) setState(() => _editFocusId = null);
  }

  Future<void> _delete(AppCatalogSelectItem item) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: item.title,
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    final cb = widget.onDelete;
    if (cb == null) return;
    await cb(item);
    if (!mounted) return;
    setState(() {
      _items = _items.where((e) => e.id != item.id).toList();
      _selected.remove(item.id);
      _editFocusId = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final items = _filtered;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop) return;
        Navigator.of(context).pop(_selected);
      },
      child: AppScaffold(
        title: Text(widget.title),
        body: Column(
          children: [
            if (widget.warningBanner != null)
              AppStatusBanner(severity: AppStatusSeverity.warning, message: widget.warningBanner!),
            if (widget.allowCreate && widget.onCreate != null)
              AppInlineAddField(
                title: widget.addFieldTitle ?? l10n.commonAdd,
                hintText: widget.addFieldTitle ?? l10n.commonAdd,
                validator: (v) => v.trim().isNotEmpty,
                invalidMessage: l10n.commonRequired,
                onSave: _create,
              ),
            if (widget.searchEnabled)
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.md,
                  AppSpacing.sm,
                  AppSpacing.md,
                  AppSpacing.sm,
                ),
                child: TextField(
                  decoration: InputDecoration(
                    labelText: l10n.commonSearch,
                    isDense: true,
                  ),
                  onChanged: (v) => setState(() => _query = v),
                ),
              ),
            Expanded(
              child: items.isEmpty
                  ? (widget.empty ??
                      EmptyPlaceholder(title: l10n.commonNothingFound))
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
                      itemCount: items.length,
                      itemBuilder: (context, index) {
                        final item = items[index];
                        final selected = _selected.contains(item.id);
                        final editing = _editFocusId == item.id;
                        final Widget trailing;
                        if (editing && _mutateEnabled) {
                          trailing = Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              if (widget.allowEdit && widget.onEdit != null)
                                IconButton(
                                  tooltip: l10n.commonEdit,
                                  icon: const Icon(Icons.edit_outlined, size: 20),
                                  padding: EdgeInsets.zero,
                                  visualDensity: VisualDensity.compact,
                                  constraints: const BoxConstraints(
                                    minWidth: AppInsets.trailingIconExtent,
                                    minHeight: AppInsets.trailingIconExtent,
                                  ),
                                  onPressed: () => _edit(item),
                                ),
                              if (widget.allowDelete && widget.onDelete != null)
                                IconButton(
                                  tooltip: l10n.commonDelete,
                                  icon: const Icon(Icons.delete_outline, size: 20),
                                  padding: EdgeInsets.zero,
                                  visualDensity: VisualDensity.compact,
                                  constraints: const BoxConstraints(
                                    minWidth: AppInsets.trailingIconExtent,
                                    minHeight: AppInsets.trailingIconExtent,
                                  ),
                                  onPressed: () => _delete(item),
                                ),
                            ],
                          );
                        } else if (widget.multiSelect) {
                          trailing = AppSwitch(
                            value: selected,
                            onChanged: item.enabled
                                ? (_) => _toggle(item.id)
                                : null,
                          );
                        } else {
                          trailing = AppRadio<String>(
                            value: item.id,
                            groupValue:
                                _selected.isEmpty ? null : _selected.first,
                            onChanged: item.enabled
                                ? (_) => _toggle(item.id)
                                : null,
                          );
                        }
                        return AppListItem(
                          borderless: true,
                          dense: true,
                          title: Text(item.title),
                          subtitle: item.subtitle != null
                              ? Text(item.subtitle!)
                              : null,
                          leading: item.effectiveLeading,
                          trailing: trailing,
                          selected: selected || editing,
                          enabled: item.enabled,
                          onTap: item.enabled ? () => _toggle(item.id) : null,
                          onLongPress: _mutateEnabled && item.enabled
                              ? () => setState(() => _editFocusId = item.id)
                              : null,
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
