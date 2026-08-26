import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_content_frame.dart';
import 'package:prodavan/features/settings/settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
/// - narrow: bottom [NavigationBar] (+ Settings as last item)
/// - medium: **left** [NavigationRail] icon over label + logo leading + Settings trailing
/// - expanded: **left** extended rail (compact on subpages unless very wide)
class AppLayout extends StatefulWidget {
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
    this.subpageOpen = false,
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

  /// True when a nested shell route (detail, settings, …) is open in the content pane.
  final bool subpageOpen;

  @override
  State<AppLayout> createState() => _AppLayoutState();
}

class _AppLayoutState extends State<AppLayout> {
  final GlobalKey<NavigatorState> _contentNavKey = GlobalKey<NavigatorState>();
  bool _settingsOpen = false;

  bool get _subpageOpen => widget.subpageOpen || _settingsOpen;

  void _openSettings() {
    if (widget.onOpenSettings != null) {
      widget.onOpenSettings!();
      return;
    }
    setState(() => _settingsOpen = true);
  }

  bool _onPopPage(Route<dynamic> route, dynamic result) {
    if (!route.didPop(result)) return false;
    if (_settingsOpen) setState(() => _settingsOpen = false);
    return true;
  }

  List<Page<void>> _contentPages() {
    final main = _wrapContent(
      Scaffold(
        primary: false,
        appBar: _hasAppBar
            ? AppBar(title: widget.title, actions: widget.actions)
            : null,
        body: widget.body,
      ),
    );

    return [
      MaterialPage<void>(
        key: const ValueKey<String>('app-layout-main'),
        child: main,
      ),
      if (_settingsOpen)
        MaterialPage<void>(
          key: const ValueKey<String>('app-layout-settings'),
          child: _wrapContent(
            const Scaffold(
              primary: false,
              body: SettingsPage(),
            ),
          ),
        ),
    ];
  }

  bool get _hasAppBar =>
      widget.title != null || (widget.actions != null && widget.actions!.isNotEmpty);

  Widget _wrapContent(Widget child) {
    if (!widget.constrainBody) return child;
    return AppContentFrame(child: child);
  }

  Widget _contentColumn() {
    return Navigator(
      key: _contentNavKey,
      pages: _contentPages(),
      onPopPage: _onPopPage,
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
        onTap: widget.onLogoTap,
        borderRadius: BorderRadius.circular(12),
        child: MouseRegion(
          cursor: widget.onLogoTap != null ? SystemMouseCursors.click : MouseCursor.defer,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
            child: _railIconLabel(extended: extended, icon: badge, label: label),
          ),
        ),
      ),
    );
  }

  Widget _settingsControl(BuildContext context, {required bool extended}) {
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
    final icon = Icon(Icons.settings_outlined, color: colors.muted, size: 24);
    final label = Text(
      l10n.settings,
      style: TextStyle(
        color: extended ? colors.onSurface : colors.muted,
        fontSize: extended ? 14 : 12,
      ),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    );

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.md),
      child: InkWell(
        onTap: _openSettings,
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
    final expanded = AppBreakpoints.railExtended(context, subpageOpen: _subpageOpen);
    final content = _contentColumn();
    final l10n = AppLocalizations.of(context);

    if (narrow) {
      final settingsIndex = widget.destinations.length;
      return Scaffold(
        body: content,
        bottomNavigationBar: NavigationBar(
          selectedIndex: widget.selectedIndex.clamp(0, widget.destinations.length - 1),
          onDestinationSelected: (i) {
            if (i == settingsIndex) {
              _openSettings();
              return;
            }
            widget.onDestinationSelected(i);
          },
          destinations: [
            for (final d in widget.destinations)
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
      selectedIndex: widget.selectedIndex,
      onDestinationSelected: widget.onDestinationSelected,
      extended: expanded,
      minWidth: _kRailMinWidth,
      minExtendedWidth: _kExtendedRailWidth,
      labelType: expanded ? NavigationRailLabelType.none : NavigationRailLabelType.all,
      trailingAtBottom: true,
      leading: expanded
          ? SizedBox(
              width: _kExtendedRailWidth,
              child: _logo(context, extended: true),
            )
          : _logo(context, extended: false),
      trailing: expanded
          ? SizedBox(
              width: _kExtendedRailWidth,
              child: _settingsControl(context, extended: true),
            )
          : _settingsControl(context, extended: false),
      destinations: [
        for (final d in widget.destinations)
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
