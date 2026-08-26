import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_content_frame.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Material default [NavigationRail.minExtendedWidth] — keep leading/trailing
/// finite; `width: infinity` under Row's unbounded max width breaks the rail.
const double _kExtendedRailWidth = 256;

/// One adaptive nav destination (bottom bar or left rail).
class AppNavDestination {
  const AppNavDestination({
    required this.icon,
    required this.label,
    this.selectedIcon,
  });

  final IconData icon;
  final IconData? selectedIcon;
  final String label;
}

/// Product chrome: adaptive nav + content column (max-width outside the rail).
///
/// - narrow: bottom [NavigationBar] (+ Settings as last item)
/// - medium: **left** [NavigationRail] icon over label + logo leading + Settings trailing
/// - expanded: **left** extended rail
class AppLayout extends StatelessWidget {
  const AppLayout({
    super.key,
    required this.body,
    required this.destinations,
    required this.selectedIndex,
    required this.onDestinationSelected,
    this.title,
    this.actions,
    this.constrainBody = true,
    this.onOpenSettings,
    this.onLogoTap,
  });

  final Widget body;
  final List<AppNavDestination> destinations;
  final int selectedIndex;
  final ValueChanged<int> onDestinationSelected;
  final Widget? title;
  final List<Widget>? actions;
  final bool constrainBody;
  final VoidCallback? onOpenSettings;
  final VoidCallback? onLogoTap;

  bool get _hasAppBar => title != null || (actions != null && actions!.isNotEmpty);

  Widget _contentColumn() {
    final page = Scaffold(
      primary: false,
      appBar: _hasAppBar ? AppBar(title: title, actions: actions) : null,
      body: body,
    );
    if (!constrainBody) return page;
    return AppContentFrame(child: page);
  }

  Widget _logo(BuildContext context, {required bool extended}) {
    final colors = context.appColors;
    final badge = Container(
      width: 28,
      height: 28,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: colors.primary.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        'AI',
        style: TextStyle(
          color: colors.primary,
          fontSize: 12,
          fontWeight: FontWeight.w700,
          height: 1,
        ),
      ),
    );
    final label = Text(
      'Prodavan',
      style: TextStyle(
        color: colors.onSurface,
        fontSize: extended ? 14 : 12,
        fontWeight: FontWeight.w600,
      ),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    );
    // Extended rail destinations are left-aligned; compact centers icon+label.
    final content = extended
        ? Row(
            children: [
              badge,
              const SizedBox(width: AppSpacing.sm),
              Expanded(child: label),
            ],
          )
        : Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              badge,
              const SizedBox(height: AppSpacing.xs),
              label,
            ],
          );

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onLogoTap,
        borderRadius: BorderRadius.circular(12),
        child: MouseRegion(
          cursor: onLogoTap != null ? SystemMouseCursors.click : MouseCursor.defer,
          child: Padding(
            padding: EdgeInsets.fromLTRB(
              extended ? AppSpacing.md : AppSpacing.sm,
              AppSpacing.md,
              extended ? AppSpacing.md : AppSpacing.sm,
              extended ? AppSpacing.md : AppSpacing.sm,
            ),
            child: content,
          ),
        ),
      ),
    );
  }

  Widget _settingsControl(BuildContext context, {required bool extended}) {
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
    final child = extended
        ? Row(
            children: [
              Icon(Icons.settings_outlined, color: colors.muted),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  l10n.settings,
                  style: TextStyle(color: colors.onSurface, fontSize: 14),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          )
        : Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.settings_outlined, color: colors.muted, size: 24),
              const SizedBox(height: AppSpacing.xs),
              Text(l10n.settings, style: TextStyle(color: colors.muted, fontSize: 12)),
            ],
          );
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.md),
      child: InkWell(
        onTap: onOpenSettings,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: EdgeInsets.symmetric(
            horizontal: extended ? AppSpacing.md : AppSpacing.sm,
            vertical: AppSpacing.sm,
          ),
          child: child,
        ),
      ),
    );
  }
  @override
  Widget build(BuildContext context) {
    final narrow = AppBreakpoints.isNarrow(context);
    final expanded = AppBreakpoints.isExpanded(context);
    final content = _contentColumn();
    final l10n = AppLocalizations.of(context);

    if (narrow) {
      final settingsIndex = destinations.length;
      return Scaffold(
        body: content,
        bottomNavigationBar: NavigationBar(
          selectedIndex: selectedIndex.clamp(0, destinations.length - 1),
          onDestinationSelected: (i) {
            if (i == settingsIndex) {
              onOpenSettings?.call();
              return;
            }
            onDestinationSelected(i);
          },
          destinations: [
            for (final d in destinations)
              NavigationDestination(
                icon: Icon(d.icon),
                selectedIcon: Icon(d.selectedIcon ?? d.icon),
                label: d.label,
              ),
            NavigationDestination(
              icon: const Icon(Icons.settings_outlined),
              label: l10n.settings,
            ),
          ],
        ),
      );
    }

    final rail = NavigationRail(
      selectedIndex: selectedIndex,
      onDestinationSelected: onDestinationSelected,
      extended: expanded,
      labelType: expanded ? NavigationRailLabelType.none : NavigationRailLabelType.all,
      // Match destination alignment: left when extended, centered when compact.
      // Finite width only — never infinity (Row gives the rail unbounded max).
      leading: expanded
          ? SizedBox(
              width: _kExtendedRailWidth,
              child: _logo(context, extended: true),
            )
          : _logo(context, extended: false),
      trailing: Expanded(
        child: Align(
          alignment: expanded ? Alignment.bottomLeft : Alignment.bottomCenter,
          child: expanded
              ? SizedBox(
                  width: _kExtendedRailWidth,
                  child: _settingsControl(context, extended: true),
                )
              : _settingsControl(context, extended: false),
        ),
      ),
      destinations: [
        for (final d in destinations)
          NavigationRailDestination(
            icon: Icon(d.icon),
            selectedIcon: Icon(d.selectedIcon ?? d.icon),
            label: Text(d.label),
          ),
      ],
    );

    return Scaffold(
      body: Row(
        children: [
          rail,
          const VerticalDivider(width: 1, thickness: 1),
          Expanded(child: content),
        ],
      ),
    );
  }
}
