import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

/// Toolbar / chrome icon action — default button kind (see docs/target/07 buttons).
class AppIconButton extends StatelessWidget {
  const AppIconButton({
    super.key,
    required this.icon,
    required this.tooltip,
    this.onPressed,
    this.selected = false,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return IconButton(
      onPressed: onPressed,
      tooltip: tooltip,
      icon: Icon(icon),
      style: IconButton.styleFrom(
        minimumSize: const Size(48, 48),
        foregroundColor: selected ? colors.primary : null,
        backgroundColor: selected ? colors.primary.withValues(alpha: 0.12) : null,
      ),
    );
  }
}

/// Two+ mode toggle (list/table, filter on/off).
class AppIconToggle extends StatelessWidget {
  const AppIconToggle({
    super.key,
    required this.icon,
    required this.tooltip,
    required this.selected,
    this.onPressed,
  });

  final IconData icon;
  final String tooltip;
  final bool selected;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return AppIconButton(
      icon: icon,
      tooltip: tooltip,
      selected: selected,
      onPressed: onPressed,
    );
  }
}
