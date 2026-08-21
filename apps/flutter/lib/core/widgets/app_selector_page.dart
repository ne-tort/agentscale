import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_checkbox.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_radio.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

class AppSelectorItem {
  const AppSelectorItem({
    required this.id,
    required this.title,
    this.subtitle,
    this.leading,
    this.trailing,
    this.tone = AppListTone.neutral,
    this.enabled = true,
  });

  final String id;
  final String title;
  final String? subtitle;
  final Widget? leading;
  final Widget? trailing;
  final AppListTone tone;
  final bool enabled;
}

/// Full-screen entity picker — replaces Dropdown / PopupMenu / modal pickers.
class AppSelectorPage extends StatefulWidget {
  const AppSelectorPage({
    super.key,
    required this.title,
    required this.items,
    this.multiSelect = false,
    this.selectedIds = const {},
    this.searchEnabled = false,
    this.showCheckboxes = false,
    this.showRadios = false,
    this.warningBanner,
    this.empty,
    this.onConfirm,
    this.popOnSelect = true,
  });

  final String title;
  final List<AppSelectorItem> items;
  final bool multiSelect;
  final Set<String> selectedIds;
  final bool searchEnabled;
  final bool showCheckboxes;
  final bool showRadios;
  final String? warningBanner;
  final Widget? empty;
  final ValueChanged<Set<String>>? onConfirm;
  final bool popOnSelect;

  @override
  State<AppSelectorPage> createState() => _AppSelectorPageState();
}

class _AppSelectorPageState extends State<AppSelectorPage> {
  late Set<String> _selected;
  String _query = '';

  @override
  void initState() {
    super.initState();
    _selected = {...widget.selectedIds};
  }

  List<AppSelectorItem> get _filtered {
    final q = _query.trim().toLowerCase();
    if (q.isEmpty) return widget.items;
    return widget.items
        .where(
          (i) =>
              i.title.toLowerCase().contains(q) ||
              (i.subtitle?.toLowerCase().contains(q) ?? false),
        )
        .toList();
  }

  void _toggle(String id) {
    setState(() {
      if (widget.multiSelect) {
        if (_selected.contains(id)) {
          _selected.remove(id);
        } else {
          _selected.add(id);
        }
      } else {
        _selected = {id};
        if (widget.popOnSelect) {
          Navigator.of(context).pop(_selected);
        }
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final items = _filtered;
    return AppScaffold(
      title: Text(widget.title),
      actions: [
        if (widget.multiSelect)
          TextButton(
            onPressed: () {
              widget.onConfirm?.call(_selected);
              Navigator.of(context).pop(_selected);
            },
            child: const Text('Готово'),
          ),
      ],
      body: Column(
        children: [
          if (widget.warningBanner != null)
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: InlineErrorBanner(message: widget.warningBanner!),
            ),
          if (widget.searchEnabled)
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.md,
                AppSpacing.sm,
                AppSpacing.md,
                AppSpacing.sm,
              ),
              child: AppTextField(
                label: 'Поиск',
                onChanged: (v) => setState(() => _query = v),
              ),
            ),
          Expanded(
            child: items.isEmpty
                ? (widget.empty ??
                    const EmptyState(title: 'Ничего не найдено'))
                : ListView.separated(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    itemCount: items.length,
                    separatorBuilder: (_, __) =>
                        const SizedBox(height: AppSpacing.sm),
                    itemBuilder: (context, index) {
                      final item = items[index];
                      final selected = _selected.contains(item.id);
                      Widget? control;
                      if (widget.showCheckboxes && widget.multiSelect) {
                        control = AppCheckbox(
                          value: selected,
                          onChanged: item.enabled
                              ? (_) => _toggle(item.id)
                              : null,
                        );
                      } else if (widget.showRadios && !widget.multiSelect) {
                        control = AppRadio<String>(
                          value: item.id,
                          groupValue:
                              _selected.isEmpty ? null : _selected.first,
                          onChanged: item.enabled
                              ? (_) => _toggle(item.id)
                              : null,
                        );
                      }
                      return AppListItem(
                        title: Text(item.title),
                        subtitle: item.subtitle != null
                            ? Text(item.subtitle!)
                            : null,
                        leading: item.leading,
                        trailing: item.trailing ??
                            (selected && !widget.showCheckboxes && !widget.showRadios
                                ? const Icon(Icons.check)
                                : null),
                        selected: selected,
                        enabled: item.enabled,
                        tone: item.tone,
                        selectionControl: control,
                        onTap: item.enabled ? () => _toggle(item.id) : null,
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
