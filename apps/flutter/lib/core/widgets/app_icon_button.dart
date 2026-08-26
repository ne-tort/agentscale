import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_insets.dart';

/// Toolbar / chrome icon action — default button kind (see docs/target/07 buttons).
///
/// Hit target and padding match list trailing chrome ([AppInsets]) so AppBar
/// actions align with radios / chevrons / inline `+` on the same right edge.
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
      icon: Icon(icon, size: 22),
      padding: EdgeInsets.zero,
      visualDensity: VisualDensity.compact,
      constraints: const BoxConstraints(
        minWidth: AppInsets.trailingIconExtent,
        minHeight: AppInsets.trailingIconExtent,
      ),
      style: IconButton.styleFrom(
        padding: EdgeInsets.zero,
        minimumSize: const Size(
          AppInsets.trailingIconExtent,
          AppInsets.trailingIconExtent,
        ),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        foregroundColor: selected ? colors.primary : colors.onSurface,
        backgroundColor: selected ? colors.primary.withValues(alpha: 0.12) : null,
      ),
    );
  }
}

/// Icon toggle (e.g. filter on/off).
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
