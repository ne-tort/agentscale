import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Placement of [EmptyPlaceholder.title] relative to the icon.
enum EmptyPlaceholderTitlePlacement {
  belowIcon,
  aboveIcon,
  startOfIcon,
  endOfIcon,
}

/// Unified empty-state for lists, selectors, tables, and inline sections.
///
/// Ported from Hiddify `EmptyPlaceholder`; uses prodavan theme tokens.
/// Prefer [onTitleTap] / [onIconTap] over instructional subtitles.
class EmptyPlaceholder extends StatelessWidget {
  const EmptyPlaceholder({
    super.key,
    this.icon = Icons.inbox_outlined,
    this.iconSize = 48,
    this.iconColor,
    this.onIconTap,
    this.title,
    this.subtitle,
    this.action,
    this.titleStyle,
    this.titleAlign = TextAlign.center,
    this.titlePlacement = EmptyPlaceholderTitlePlacement.belowIcon,
    this.onTitleTap,
    this.child,
    this.padding = const EdgeInsets.all(AppSpacing.xl),
    this.fillViewport = true,
  });

  final IconData icon;
  final double iconSize;
  final Color? iconColor;
  final VoidCallback? onIconTap;

  final String? title;
  final String? subtitle;
  final Widget? action;
  final TextStyle? titleStyle;
  final TextAlign titleAlign;
  final EmptyPlaceholderTitlePlacement titlePlacement;
  final VoidCallback? onTitleTap;

  final Widget? child;
  final EdgeInsetsGeometry padding;

  /// When true (default), vertically centers in bounded or viewport height.
  /// Set false for inline section empties inside scrollable forms.
  final bool fillViewport;

  /// [SliverFillRemaining] wrapper for [CustomScrollView] empty bodies.
  static Widget sliver({
    Key? key,
    IconData icon = Icons.inbox_outlined,
    double iconSize = 48,
    Color? iconColor,
    VoidCallback? onIconTap,
    String? title,
    String? subtitle,
    Widget? action,
    TextStyle? titleStyle,
    TextAlign titleAlign = TextAlign.center,
    EmptyPlaceholderTitlePlacement titlePlacement = EmptyPlaceholderTitlePlacement.belowIcon,
    VoidCallback? onTitleTap,
    Widget? child,
    EdgeInsetsGeometry padding = const EdgeInsets.all(AppSpacing.xl),
  }) {
    return SliverFillRemaining(
      hasScrollBody: false,
      child: EmptyPlaceholder(
        key: key,
        icon: icon,
        iconSize: iconSize,
        iconColor: iconColor,
        onIconTap: onIconTap,
        title: title,
        subtitle: subtitle,
        action: action,
        titleStyle: titleStyle,
        titleAlign: titleAlign,
        titlePlacement: titlePlacement,
        onTitleTap: onTitleTap,
        padding: padding,
        child: child,
      ),
    );
  }

  Widget? _buildExtraContent(BuildContext context) {
    if (child != null) return child;
    if (subtitle == null && action == null) return null;
    final colors = context.appColors;
    final theme = Theme.of(context);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (subtitle != null) ...[
          Text(
            subtitle!,
            style: theme.textTheme.bodyMedium?.copyWith(color: colors.muted),
            textAlign: titleAlign,
          ),
        ],
        if (action != null) ...[
          const SizedBox(height: AppSpacing.lg),
          action!,
        ],
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final resolvedIconColor = iconColor ?? colors.border;
    final resolvedTitleStyle = titleStyle ??
        theme.textTheme.titleMedium?.copyWith(
          fontWeight: FontWeight.w700,
          color: theme.colorScheme.onSurfaceVariant,
        );

    Widget iconWidget = Icon(icon, size: iconSize, color: resolvedIconColor);
    if (onIconTap != null) {
      iconWidget = InkWell(
        onTap: onIconTap,
        customBorder: const CircleBorder(),
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.sm),
          child: iconWidget,
        ),
      );
    }

    Widget? titleWidget;
    if (title != null && title!.isNotEmpty) {
      titleWidget = Text(title!, style: resolvedTitleStyle, textAlign: titleAlign);
      if (onTitleTap != null) {
        titleWidget = InkWell(
          onTap: onTitleTap,
          borderRadius: BorderRadius.circular(8),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: AppSpacing.xs),
            child: titleWidget,
          ),
        );
      }
    }

    final extra = _buildExtraContent(context);

    final Widget content = switch (titlePlacement) {
      EmptyPlaceholderTitlePlacement.belowIcon => Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            iconWidget,
            if (titleWidget != null) ...[
              const SizedBox(height: AppSpacing.md),
              titleWidget,
            ],
            if (extra != null) ...[
              const SizedBox(height: AppSpacing.md),
              extra,
            ],
          ],
        ),
      EmptyPlaceholderTitlePlacement.aboveIcon => Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (titleWidget != null) ...[
              titleWidget,
              const SizedBox(height: AppSpacing.md),
            ],
            iconWidget,
            if (extra != null) ...[
              const SizedBox(height: AppSpacing.md),
              extra,
            ],
          ],
        ),
      EmptyPlaceholderTitlePlacement.startOfIcon => Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (titleWidget != null) ...[
                  Flexible(child: titleWidget),
                  const SizedBox(width: AppSpacing.sm),
                ],
                iconWidget,
              ],
            ),
            if (extra != null) ...[
              const SizedBox(height: AppSpacing.md),
              extra,
            ],
          ],
        ),
      EmptyPlaceholderTitlePlacement.endOfIcon => Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                iconWidget,
                if (titleWidget != null) ...[
                  const SizedBox(width: AppSpacing.sm),
                  Flexible(child: titleWidget),
                ],
              ],
            ),
            if (extra != null) ...[
              const SizedBox(height: AppSpacing.md),
              extra,
            ],
          ],
        ),
    };

    final padded = Padding(padding: padding, child: content);
    final centered = Center(child: padded);

    if (!fillViewport) {
      return centered;
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.hasBoundedHeight && constraints.maxHeight.isFinite) {
          return SizedBox(
            width: constraints.maxWidth.isFinite ? constraints.maxWidth : null,
            height: constraints.maxHeight,
            child: centered,
          );
        }

        final mq = MediaQuery.of(context);
        final minH = (mq.size.height - mq.padding.vertical - kToolbarHeight - 24)
            .clamp(240.0, mq.size.height);
        return SizedBox(
          width: constraints.maxWidth.isFinite ? constraints.maxWidth : null,
          height: minH,
          child: centered,
        );
      },
    );
  }
}
