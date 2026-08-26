import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';

/// Opens [AppCatalogSelectPage] and saves selection seamlessly.
class AppChoicePreference<T> extends StatelessWidget {
  const AppChoicePreference({
    super.key,
    required this.title,
    required this.value,
    required this.choices,
    required this.keyFor,
    required this.labelFor,
    required this.onSave,
    this.icon,
    this.iconFor,
    this.enabled = true,
    this.multiSelect = false,
    this.pickerTitle,
    this.presentValue,
  });

  final String title;
  final T value;
  final List<T> choices;
  final String Function(T value) keyFor;
  final String Function(T value) labelFor;
  final Future<void> Function(T value) onSave;
  final IconData? icon;
  final IconData? Function(T value)? iconFor;
  final bool enabled;
  final bool multiSelect;
  final String? pickerTitle;
  final String Function(T value)? presentValue;

  Future<void> _pick(BuildContext context) async {
    if (!enabled) return;
    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppCatalogSelectPage(
          title: pickerTitle ?? title,
          multiSelect: multiSelect,
          selectedIds: {keyFor(value)},
          items: [
            for (final c in choices)
              AppCatalogSelectItem(
                id: keyFor(c),
                title: labelFor(c),
                icon: iconFor?.call(c),
              ),
          ],
        ),
      ),
    );
    if (picked == null || picked.isEmpty) return;
    for (final c in choices) {
      if (keyFor(c) == picked.first) {
        try {
          await onSave(c);
        } catch (e) {
          if (context.mounted) AppErrors.showSnack(context, e);
        }
        return;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppPreferenceTile(
      title: title,
      icon: icon,
      enabled: enabled,
      subtitle: Text(presentValue?.call(value) ?? labelFor(value)),
      trailing: const AppTrailingChevron(),
      onTap: () => _pick(context),
    );
  }
}
