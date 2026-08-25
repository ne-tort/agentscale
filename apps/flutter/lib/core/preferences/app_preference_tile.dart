import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

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
    final colors = context.appColors;
    final accent = accentColor ?? colors.onSurface;
    return ListTile(
      enabled: enabled,
      onTap: onTap,
      leading: leading ?? (icon != null ? Icon(icon, color: accent) : null),
      title: Text(title, style: TextStyle(color: accent)),
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
  });

  final VoidCallback onSave;
  final VoidCallback onCancel;
  final VoidCallback? onToggleObscure;
  final bool? obscured;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconButton(
          icon: const Icon(Icons.check_rounded),
          tooltip: MaterialLocalizations.of(context).okButtonLabel,
          onPressed: onSave,
        ),
        IconButton(
          icon: const Icon(Icons.close_rounded),
          tooltip: MaterialLocalizations.of(context).cancelButtonLabel,
          onPressed: onCancel,
        ),
        if (onToggleObscure != null)
          IconButton(
            icon: Icon(
              obscured == true
                  ? Icons.visibility_off_rounded
                  : Icons.visibility_rounded,
            ),
            onPressed: onToggleObscure,
          ),
      ],
    );
  }
}
