import 'package:flutter/material.dart';

enum AppButtonVariant { filled, outlined, text }
enum AppButtonSize { compact, regular }

class AppButton extends StatelessWidget {
  const AppButton({
    super.key,
    required this.label,
    this.onPressed,
    this.variant = AppButtonVariant.filled,
    this.size = AppButtonSize.regular,
    this.icon,
    this.expanded = true,
  });

  final String label;
  final VoidCallback? onPressed;
  final AppButtonVariant variant;
  final AppButtonSize size;
  final IconData? icon;
  final bool expanded;

  @override
  Widget build(BuildContext context) {
    final child = icon == null
        ? Text(label)
        : Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 18),
              const SizedBox(width: 8),
              Text(label),
            ],
          );

    final style = size == AppButtonSize.compact
        ? const ButtonStyle(minimumSize: WidgetStatePropertyAll(Size(0, 40)))
        : null;

    Widget button;
    switch (variant) {
      case AppButtonVariant.filled:
        button = FilledButton(onPressed: onPressed, style: style, child: child);
      case AppButtonVariant.outlined:
        button = OutlinedButton(onPressed: onPressed, style: style, child: child);
      case AppButtonVariant.text:
        button = TextButton(onPressed: onPressed, style: style, child: child);
    }
    if (!expanded) return button;
    return SizedBox(width: double.infinity, child: button);
  }
}

class AppAsyncButton extends StatelessWidget {
  const AppAsyncButton({
    super.key,
    required this.label,
    required this.busy,
    this.onPressed,
    this.variant = AppButtonVariant.filled,
  });

  final String label;
  final bool busy;
  final VoidCallback? onPressed;
  final AppButtonVariant variant;

  @override
  Widget build(BuildContext context) {
    if (busy) {
      return const SizedBox(
        height: 48,
        child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
      );
    }
    return AppButton(
      label: label,
      onPressed: onPressed,
      variant: variant,
    );
  }
}
