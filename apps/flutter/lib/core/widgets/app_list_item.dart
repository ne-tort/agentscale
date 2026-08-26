import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_insets.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

enum AppListTone { neutral, warning, danger, success }

/// Universal list row — replaces ad-hoc ListTile / menu rows.
class AppListItem extends StatelessWidget {
  const AppListItem({
    super.key,
    required this.title,
    this.subtitle,
    this.leading,
    this.trailing,
    this.selected = false,
    this.enabled = true,
    this.tone = AppListTone.neutral,
    this.banner,
    this.onTap,
    this.onLongPress,
    this.selectionControl,
    this.dense = false,
    this.semanticLabel,
    this.borderless = false,
  });

  final Widget title;
  final Widget? subtitle;
  final Widget? leading;
  final Widget? trailing;
  final bool selected;
  final bool enabled;
  final AppListTone tone;
  final String? banner;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;
  final Widget? selectionControl;
  final bool dense;
  final String? semanticLabel;

  /// Flat row without card border (catalog/table-like pickers).
  final bool borderless;

  @override
  Widget build(BuildContext context) {
    final tokens = Theme.of(context).extension<AppColorTokens>()!;
    final bg = borderless
        ? (selected ? tokens.primary.withValues(alpha: 0.08) : Colors.transparent)
        : (selected ? tokens.primary.withValues(alpha: 0.08) : _toneBg(tokens));
    final border = borderless
        ? BorderSide.none
        : BorderSide(color: selected ? tokens.primary : _toneBorder(tokens));
    final leftPad = dense ? AppSpacing.sm : AppSpacing.md;

    final trailingSlot = trailing ?? selectionControl;

    final child = Material(
      color: bg,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(borderless ? 0 : 8),
        side: border,
      ),
      child: InkWell(
        onTap: enabled ? onTap : null,
        onLongPress: enabled ? onLongPress : null,
        borderRadius: BorderRadius.circular(borderless ? 0 : 8),
        child: Padding(
          padding: EdgeInsets.only(
            left: leftPad,
            right: AppInsets.trailingActionRight,
            top: dense ? AppSpacing.sm : AppSpacing.md,
            bottom: dense ? AppSpacing.sm : AppSpacing.md,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (banner != null && banner!.isNotEmpty) ...[
                Text(
                  banner!,
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: tokens.warning,
                      ),
                ),
                const SizedBox(height: AppSpacing.xs),
              ],
              Row(
                children: [
                  if (leading != null) ...[
                    leading!,
                    const SizedBox(width: AppSpacing.sm),
                  ],
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        DefaultTextStyle.merge(
                          style: Theme.of(context).textTheme.titleMedium?.copyWith(
                                color: enabled ? null : tokens.muted,
                              ),
                          child: title,
                        ),
                        if (subtitle != null) ...[
                          const SizedBox(height: AppSpacing.xs),
                          DefaultTextStyle.merge(
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                  color: tokens.muted,
                                ),
                            child: subtitle!,
                          ),
                        ],
                      ],
                    ),
                  ),
                  if (trailingSlot != null) ...[
                    const SizedBox(width: AppSpacing.sm),
                    trailingSlot,
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );

    if (semanticLabel != null) {
      return Semantics(label: semanticLabel, button: onTap != null, child: child);
    }
    return child;
  }

  Color _toneBg(AppColorTokens t) {
    return switch (tone) {
      AppListTone.neutral => t.surface,
      AppListTone.warning => t.warning.withValues(alpha: 0.08),
      AppListTone.danger => t.dangerContainer,
      AppListTone.success => t.success.withValues(alpha: 0.08),
    };
  }

  Color _toneBorder(AppColorTokens t) {
    return switch (tone) {
      AppListTone.neutral => t.border,
      AppListTone.warning => t.warning.withValues(alpha: 0.4),
      AppListTone.danger => t.danger.withValues(alpha: 0.4),
      AppListTone.success => t.success.withValues(alpha: 0.4),
    };
  }
}
