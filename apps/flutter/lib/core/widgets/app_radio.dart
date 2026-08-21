import 'package:flutter/material.dart';

enum AppRadioVariant { material, tonal }

class AppRadio<T> extends StatelessWidget {
  const AppRadio({
    super.key,
    required this.value,
    required this.groupValue,
    this.onChanged,
    this.label,
    this.variant = AppRadioVariant.material,
    this.semanticLabel,
  });

  final T value;
  final T? groupValue;
  final ValueChanged<T?>? onChanged;
  final String? label;
  final AppRadioVariant variant;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final radio = Radio<T>(
      value: value,
      groupValue: groupValue,
      onChanged: onChanged,
      toggleable: false,
    );
    if (label == null) return radio;
    return InkWell(
      onTap: onChanged == null ? null : () => onChanged!(value),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          radio,
          Flexible(child: Text(label!)),
        ],
      ),
    );
  }
}
