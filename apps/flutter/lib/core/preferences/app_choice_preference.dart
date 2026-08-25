import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/widgets/app_selector_page.dart';

/// Opens [AppSelectorPage] and saves selection seamlessly.
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
    this.showRadios = true,
    this.multiSelect = false,
    this.pickerTitle,
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
  final bool showRadios;
  final bool multiSelect;
  final String? pickerTitle;

  Future<void> _pick(BuildContext context) async {
    if (!enabled) return;
    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppSelectorPage(
          title: pickerTitle ?? title,
          showRadios: showRadios && !multiSelect,
          showCheckboxes: multiSelect,
          multiSelect: multiSelect,
          selectedIds: {keyFor(value)},
          items: [
            for (final c in choices)
              AppSelectorItem(
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
        await onSave(c);
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
      subtitle: Text(labelFor(value)),
      trailing: const Icon(Icons.chevron_right),
      onTap: () => _pick(context),
    );
  }
}
