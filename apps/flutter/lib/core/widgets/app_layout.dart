import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';

/// One adaptive nav destination (bottom bar or right rail).
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
/// - narrow: bottom [NavigationBar]
/// - medium: right [NavigationRail] with icon above label
/// - expanded: right extended [NavigationRail] (icon + label inline)
///
/// Set [constrainBody] false when nested pages already use [AppScaffold]
/// (so their AppBar can span the content column next to the rail).
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
  });

  final Widget body;
  final List<AppNavDestination> destinations;
  final int selectedIndex;
  final ValueChanged<int> onDestinationSelected;
  final Widget? title;
  final List<Widget>? actions;
  final bool constrainBody;

  Widget _wrapContent(Widget child) {
    if (!constrainBody) return child;
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: AppBreakpoints.contentMaxWidth),
        child: SizedBox(width: double.infinity, child: child),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final narrow = AppBreakpoints.isNarrow(context);
    final expanded = AppBreakpoints.isExpanded(context);
    final content = _wrapContent(body);

    PreferredSizeWidget? appBar;
    if (title != null || (actions != null && actions!.isNotEmpty)) {
      appBar = AppBar(title: title, actions: actions);
    }

    if (narrow) {
      return Scaffold(
        appBar: appBar,
        body: content,
        bottomNavigationBar: NavigationBar(
          selectedIndex: selectedIndex,
          onDestinationSelected: onDestinationSelected,
          destinations: [
            for (final d in destinations)
              NavigationDestination(
                icon: Icon(d.icon),
                selectedIcon: Icon(d.selectedIcon ?? d.icon),
                label: d.label,
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
      appBar: appBar,
      body: Row(
        children: [
          Expanded(child: content),
          const VerticalDivider(width: 1, thickness: 1),
          rail,
        ],
      ),
    );
  }
}
