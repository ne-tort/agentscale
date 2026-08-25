import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
  });

  final Widget body;
  final List<AppNavDestination> destinations;
  final int selectedIndex;
  final ValueChanged<int> onDestinationSelected;
  final Widget? title;
  final List<Widget>? actions;
  final bool constrainBody;
  final VoidCallback? onOpenSettings;

  bool get _hasAppBar => title != null || (actions != null && actions!.isNotEmpty);

  Widget _contentColumn() {
    final page = Scaffold(
      primary: false,
      appBar: _hasAppBar ? AppBar(title: title, actions: actions) : null,
      body: body,
    );
    if (!constrainBody) return page;
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: AppBreakpoints.contentMaxWidth),
        child: SizedBox(width: double.infinity, height: double.infinity, child: page),
      ),
    );
  }

  Widget _logo(BuildContext context, {required bool extended}) {
    final colors = context.appColors;
    final icon = Icon(Icons.auto_awesome, color: colors.primary, size: 24);
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
    if (extended) {
      return Padding(
        padding: const EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.lg, AppSpacing.md, AppSpacing.md),
        child: Row(
          children: [
            icon,
            const SizedBox(width: AppSpacing.sm),
            Flexible(child: label),
          ],
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.md),
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

  Widget _settingsControl(BuildContext context, {required bool extended}) {
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
    final child = extended
        ? Row(
            children: [
              Icon(Icons.settings_outlined, color: colors.muted),
              const SizedBox(width: AppSpacing.sm),
              Text(l10n.settings, style: TextStyle(color: colors.onSurface, fontSize: 14)),
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
      leading: _logo(context, extended: expanded),
      trailing: Expanded(
        child: Align(
          alignment: Alignment.bottomCenter,
          child: _settingsControl(context, extended: expanded),
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
