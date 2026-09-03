import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_content_frame.dart';

/// Material defaults — keep leading/trailing on the same icon column as destinations.
const double _kRailMinWidth = 80;
const double _kExtendedRailWidth = 256;
const double _kRailIconLabelGap = 8; // Material `_horizontalDestinationPadding`
const double _kLogoBadgeSize = 36;

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
/// - narrow: bottom [NavigationBar] (destinations may include Settings)
/// - medium+: left [NavigationRail]; optional [trailingDestination] pinned at bottom
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
    this.onLogoTap,
    this.subpageOpen = false,
    this.trailingDestination,
    this.trailingSelected = false,
    this.onTrailingSelected,
    this.railExtra,
  });

  final Widget body;
  final List<AppNavDestination> destinations;
  /// Selected index among [destinations] only (not trailing). Null = none (e.g. overview via logo).
  final int? selectedIndex;
  final ValueChanged<int> onDestinationSelected;
  final Widget? title;
  final List<Widget>? actions;
  final bool constrainBody;
  final VoidCallback? onLogoTap;

  /// Pinned at bottom of left rail (desktop). Shell switches content via [onTrailingSelected].
  final AppNavDestination? trailingDestination;
  final bool trailingSelected;
  final VoidCallback? onTrailingSelected;

  /// Optional block under the first destination (e.g. Chats under Projects).
  final Widget? railExtra;

  /// True when a nested shell route (detail, …) is open in the content pane.
  final bool subpageOpen;

  bool get _hasAppBar =>
      title != null || (actions != null && actions!.isNotEmpty);

  Widget _wrapContent(Widget child) {
    if (!constrainBody) return child;
    return AppContentFrame(child: child);
  }

  Widget _contentColumn() {
    return _wrapContent(
      Scaffold(
        primary: false,
        appBar: _hasAppBar
            ? AppBar(title: title, actions: actions)
            : null,
        body: body,
      ),
    );
  }

  /// Same horizontal geometry as [NavigationRail] destinations: icon centered in
  /// [minWidth], then label (extended only).
  Widget _railIconLabel({
    required bool extended,
    required Widget icon,
    required Widget label,
  }) {
    if (!extended) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            icon,
            const SizedBox(height: AppSpacing.xs),
            label,
          ],
        ),
      );
    }
    return Row(
      children: [
        SizedBox(
          width: _kRailMinWidth,
          child: Center(child: icon),
        ),
        Expanded(child: label),
        const SizedBox(width: _kRailIconLabelGap),
      ],
    );
  }

  Widget _logo(BuildContext context, {required bool extended}) {
    final colors = context.appColors;
    final badge = Container(
      width: _kLogoBadgeSize,
      height: _kLogoBadgeSize,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: colors.primary.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(
        'AI',
        style: TextStyle(
          color: colors.primary,
          fontSize: 14,
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

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onLogoTap,
        borderRadius: BorderRadius.circular(12),
        child: MouseRegion(
          cursor: onLogoTap != null ? SystemMouseCursors.click : MouseCursor.defer,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            child: _railIconLabel(extended: extended, icon: badge, label: label),
          ),
        ),
      ),
    );
  }

  Widget _trailingControl(
    BuildContext context, {
    required AppNavDestination destination,
    required bool extended,
    required bool selected,
  }) {
    final colors = context.appColors;
    final icon = Icon(
      destination.icon,
      color: selected ? colors.primary : colors.muted,
      size: 24,
    );
    final label = Text(
      destination.label,
      style: TextStyle(
        color: selected
            ? colors.primary
            : (extended ? colors.onSurface : colors.muted),
        fontSize: extended ? 14 : 12,
        fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
      ),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    );

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.md),
      child: InkWell(
        onTap: onTrailingSelected,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
          child: _railIconLabel(extended: extended, icon: icon, label: label),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final narrow = AppBreakpoints.isNarrow(context);
    final expanded = AppBreakpoints.railExtended(context, subpageOpen: subpageOpen);
    final content = _contentColumn();
    final mainSelected = selectedIndex == null
        ? null
        : (destinations.isEmpty
            ? 0
            : selectedIndex!.clamp(0, destinations.length - 1));

    if (narrow) {
      final allDestinations = [
        ...destinations,
        if (trailingDestination != null) trailingDestination!,
      ];
      final selected = trailingSelected && trailingDestination != null
          ? allDestinations.length - 1
          : (mainSelected ?? 0).clamp(0, allDestinations.length - 1);

      return Scaffold(
        body: content,
        bottomNavigationBar: NavigationBar(
          selectedIndex: selected,
          onDestinationSelected: (i) {
            if (trailingDestination != null && i == allDestinations.length - 1) {
              onTrailingSelected?.call();
              return;
            }
            onDestinationSelected(i);
          },
          destinations: [
            for (final d in allDestinations)
              NavigationDestination(
                icon: Icon(d.icon),
                selectedIcon: Icon(d.selectedIcon ?? d.icon),
                label: d.label,
              ),
          ],
        ),
      );
    }

    final railWidth = expanded ? _kExtendedRailWidth : _kRailMinWidth;
    final Widget rail;
    if (railExtra == null) {
      rail = NavigationRail(
        selectedIndex: mainSelected,
        onDestinationSelected: onDestinationSelected,
        extended: expanded,
        minWidth: _kRailMinWidth,
        minExtendedWidth: _kExtendedRailWidth,
        labelType: expanded ? NavigationRailLabelType.none : NavigationRailLabelType.all,
        trailingAtBottom: trailingDestination != null,
        leading: expanded
            ? SizedBox(
                width: _kExtendedRailWidth,
                child: _logo(context, extended: true),
              )
            : _logo(context, extended: false),
        trailing: trailingDestination == null
            ? null
            : expanded
                ? SizedBox(
                    width: _kExtendedRailWidth,
                    child: _trailingControl(
                      context,
                      destination: trailingDestination!,
                      extended: true,
                      selected: trailingSelected,
                    ),
                  )
                : _trailingControl(
                    context,
                    destination: trailingDestination!,
                    extended: false,
                    selected: trailingSelected,
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
    } else {
      rail = SizedBox(
        width: railWidth,
        child: Material(
          color: Theme.of(context).navigationRailTheme.backgroundColor
              ?? Theme.of(context).colorScheme.surface,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              expanded
                  ? SizedBox(width: railWidth, child: _logo(context, extended: true))
                  : _logo(context, extended: false),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
                  children: [
                    for (var i = 0; i < destinations.length; i++) ...[
                      _destinationControl(
                        context,
                        destination: destinations[i],
                        extended: expanded,
                        selected: mainSelected == i,
                        onTap: () => onDestinationSelected(i),
                      ),
                      if (i == 0) ...[
                        const SizedBox(height: AppSpacing.sm),
                        railExtra!,
                        const SizedBox(height: AppSpacing.sm),
                      ],
                    ],
                  ],
                ),
              ),
              if (trailingDestination != null)
                expanded
                    ? SizedBox(
                        width: railWidth,
                        child: _trailingControl(
                          context,
                          destination: trailingDestination!,
                          extended: true,
                          selected: trailingSelected,
                        ),
                      )
                    : _trailingControl(
                        context,
                        destination: trailingDestination!,
                        extended: false,
                        selected: trailingSelected,
                      ),
            ],
          ),
        ),
      );
    }

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

  Widget _destinationControl(
    BuildContext context, {
    required AppNavDestination destination,
    required bool extended,
    required bool selected,
    required VoidCallback onTap,
  }) {
    final colors = context.appColors;
    final icon = Icon(
      selected ? (destination.selectedIcon ?? destination.icon) : destination.icon,
      color: selected ? colors.primary : colors.muted,
      size: 24,
    );
    final label = Text(
      destination.label,
      style: TextStyle(
        color: selected
            ? colors.primary
            : (extended ? colors.onSurface : colors.muted),
        fontSize: extended ? 14 : 12,
        fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
      ),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    );

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        child: _railIconLabel(extended: extended, icon: icon, label: label),
      ),
    );
  }
}
