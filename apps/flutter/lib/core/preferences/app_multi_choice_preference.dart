import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';

/// Multi-select via [AppCatalogSelectPage] (switch trailing) with seamless save.
class AppMultiChoicePreference<T> extends StatelessWidget {
  const AppMultiChoicePreference({
    super.key,
    required this.title,
    required this.values,
    required this.choices,
    required this.keyFor,
    required this.labelFor,
    required this.onSave,
    this.icon,
    this.iconFor,
    this.enabled = true,
    this.pickerTitle,
    this.presentValues,
  });

  final String title;
  final Set<T> values;
  final List<T> choices;
  final String Function(T value) keyFor;
  final String Function(T value) labelFor;
  final Future<void> Function(Set<T> values) onSave;
  final IconData? icon;
  final IconData? Function(T value)? iconFor;
  final bool enabled;
  final String? pickerTitle;
  final String Function(Set<T> values)? presentValues;

  String _subtitle() {
    if (presentValues != null) return presentValues!(values);
    if (values.isEmpty) return '';
    if (values.length == 1) return labelFor(values.first);
    return '${values.length}';
  }

  Set<T> _resolve(Set<String> picked) {
    final next = <T>{};
    for (final c in choices) {
      if (picked.contains(keyFor(c))) next.add(c);
    }
    return next;
  }

  Future<void> _pick(BuildContext context) async {
    if (!enabled) return;
    await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppCatalogSelectPage(
          title: pickerTitle ?? title,
          multiSelect: true,
          selectedIds: values.map(keyFor).toSet(),
          items: [
            for (final c in choices)
              AppCatalogSelectItem(
                id: keyFor(c),
                title: labelFor(c),
                icon: iconFor?.call(c),
              ),
          ],
          onConfirm: (picked) async {
            try {
              await onSave(_resolve(picked));
            } catch (e) {
              if (context.mounted) AppErrors.showSnack(context, e);
            }
          },
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final text = _subtitle();
    return AppPreferenceTile(
      title: title,
      icon: icon,
      enabled: enabled,
      subtitle: text.isEmpty ? null : Text(text),
      trailing: const AppTrailingChevron(),
      onTap: () => _pick(context),
    );
  }
}
