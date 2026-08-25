import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';

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
/// - narrow: bottom [NavigationBar]
/// - medium: **left** [NavigationRail] with icon above label
/// - expanded: **left** extended [NavigationRail] (icon + label inline)
///
/// AppBar from [title]/[actions] sits inside the content max-width column.
/// Set [constrainBody] false when nested pages already use [AppScaffold]
/// (their AppBar+body are constrained together there).
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

  @override
  Widget build(BuildContext context) {
    final narrow = AppBreakpoints.isNarrow(context);
    final expanded = AppBreakpoints.isExpanded(context);
    final content = _contentColumn();

    if (narrow) {
      return Scaffold(
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
