import 'package:flutter/material.dart';

enum AppCheckboxVariant { material, filledSquare, tonal }

class AppCheckbox extends StatelessWidget {
  const AppCheckbox({
    super.key,
    required this.value,
    this.onChanged,
    this.label,
    this.variant = AppCheckboxVariant.material,
    this.tristate = false,
    this.semanticLabel,
  });

  final bool? value;
  final ValueChanged<bool?>? onChanged;
  final String? label;
  final AppCheckboxVariant variant;
  final bool tristate;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final checkbox = Checkbox(
      value: value,
      tristate: tristate,
      onChanged: onChanged,
      fillColor: variant == AppCheckboxVariant.tonal
          ? WidgetStateProperty.resolveWith((states) {
              if (states.contains(WidgetState.selected)) {
                return Theme.of(context).colorScheme.secondaryContainer;
              }
              return null;
            })
          : null,
    );
    if (label == null) return checkbox;
    return InkWell(
      onTap: onChanged == null
          ? null
          : () => onChanged!(!(value ?? false)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          checkbox,
          Flexible(child: Text(label!)),
        ],
      ),
    );
  }
}
