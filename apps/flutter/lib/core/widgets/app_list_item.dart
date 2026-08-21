import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
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
    this.selectionControl,
    this.dense = false,
    this.semanticLabel,
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
  final Widget? selectionControl;
  final bool dense;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final tokens = Theme.of(context).extension<AppColorTokens>()!;
    final bg = _toneBg(tokens);
    final border = _toneBorder(tokens);
    final pad = dense ? AppSpacing.sm : AppSpacing.md;

    final child = Material(
      color: selected ? tokens.primary.withValues(alpha: 0.08) : bg,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(8),
        side: BorderSide(color: selected ? tokens.primary : border),
      ),
      child: InkWell(
        onTap: enabled ? onTap : null,
        borderRadius: BorderRadius.circular(8),
        child: Padding(
          padding: EdgeInsets.all(pad),
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
                  if (selectionControl != null) ...[
                    selectionControl!,
                    const SizedBox(width: AppSpacing.sm),
                  ] else if (leading != null) ...[
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
                  if (trailing != null) ...[
                    const SizedBox(width: AppSpacing.sm),
                    trailing!,
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
