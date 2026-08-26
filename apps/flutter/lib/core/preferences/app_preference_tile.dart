import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_insets.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
/// Base row chrome for preference controls (settings / admin forms).
class AppPreferenceTile extends StatelessWidget {
  const AppPreferenceTile({
    super.key,
    required this.title,
    this.subtitle,
    this.icon,
    this.leading,
    this.trailing,
    this.enabled = true,
    this.onTap,
    this.accentColor,
  });

  final String title;
  final Widget? subtitle;
  final IconData? icon;
  final Widget? leading;
  final Widget? trailing;
  final bool enabled;
  final VoidCallback? onTap;
  final Color? accentColor;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final titleStyle = accentColor != null
        ? theme.textTheme.titleMedium?.copyWith(color: accentColor)
        : theme.textTheme.titleMedium;

    return ListTile(
      enabled: enabled,
      onTap: onTap,
      contentPadding: const EdgeInsets.only(
        left: AppSpacing.md,
        right: AppInsets.trailingActionRight,
      ),
      minLeadingWidth: 32,
      horizontalTitleGap: AppSpacing.md,
      leading: leading ??
          (icon != null
              ? Icon(
                  icon,
                  size: 24,
                  color: enabled
                      ? Theme.of(context).colorScheme.onSurfaceVariant
                      : Theme.of(context).disabledColor,
                )
              : null),
      title: Text(title, style: titleStyle),
      subtitle: subtitle,
      trailing: trailing,
    );
  }
}

/// Inline edit action buttons (check / close / visibility).
class AppPreferenceInlineActions extends StatelessWidget {
  const AppPreferenceInlineActions({
    super.key,
    required this.onSave,
    required this.onCancel,
    this.onToggleObscure,
    this.obscured,
    this.onGuardBlur,
  });

  final VoidCallback onSave;
  final VoidCallback onCancel;
  final VoidCallback? onToggleObscure;
  final bool? obscured;
  final VoidCallback? onGuardBlur;

  Widget _wrap(VoidCallback? guard, Widget child) {
    if (guard == null) return child;
    return Listener(
      onPointerDown: (_) => guard(),
      child: child,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _wrap(
          onGuardBlur,
          IconButton(
            icon: const Icon(Icons.check_rounded, size: 22),
            visualDensity: VisualDensity.compact,
            tooltip: MaterialLocalizations.of(context).okButtonLabel,
            onPressed: onSave,
          ),
        ),
        _wrap(
          onGuardBlur,
          IconButton(
            icon: const Icon(Icons.close_rounded, size: 22),
            visualDensity: VisualDensity.compact,
            tooltip: MaterialLocalizations.of(context).cancelButtonLabel,
            onPressed: onCancel,
          ),
        ),
        if (onToggleObscure != null)
          _wrap(
            onGuardBlur,
            IconButton(
              icon: Icon(
                obscured == true
                    ? Icons.visibility_off_rounded
                    : Icons.visibility_rounded,
                size: 22,
              ),
              visualDensity: VisualDensity.compact,
              onPressed: onToggleObscure,
            ),
          ),
      ],
    );
  }
}
