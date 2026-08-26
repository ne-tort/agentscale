import 'package:flutter/material.dart';

/// Compact switch for catalog/selector trailing slot.
class AppSwitch extends StatelessWidget {
  const AppSwitch({
    super.key,
    required this.value,
    this.onChanged,
    this.semanticLabel,
  });

  final bool value;
  final ValueChanged<bool>? onChanged;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final sw = Switch(
      value: value,
      onChanged: onChanged,
      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
    );
    if (semanticLabel == null) return sw;
    return Semantics(label: semanticLabel, child: sw);
  }
}
