import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';

/// Back-compat item model — prefer [AppCatalogSelectItem].
class AppSelectorItem {
  const AppSelectorItem({
    required this.id,
    required this.title,
    this.subtitle,
    this.icon,
    this.leading,
    this.trailing,
    this.tone = AppListTone.neutral,
    this.enabled = true,
  });

  final String id;
  final String title;
  final String? subtitle;
  final IconData? icon;
  final Widget? leading;
  final Widget? trailing;
  final AppListTone tone;
  final bool enabled;

  Widget? get effectiveLeading =>
      leading ?? (icon != null ? Icon(icon) : null);

  AppCatalogSelectItem toCatalogItem() => AppCatalogSelectItem(
        id: id,
        title: title,
        subtitle: subtitle,
        icon: icon,
        leading: leading,
        enabled: enabled,
      );
}

/// Full-screen picker — thin wrapper over [AppCatalogSelectPage].
class AppSelectorPage extends StatelessWidget {
  const AppSelectorPage({
    super.key,
    required this.title,
    required this.items,
    this.multiSelect = false,
    this.selectedIds = const {},
    this.searchEnabled = false,
    this.showCheckboxes = false,
    this.showRadios = false,
    this.warningBanner,
    this.empty,
    this.onConfirm,
    this.popOnSelect = true,
  });

  final String title;
  final List<AppSelectorItem> items;
  final bool multiSelect;
  final Set<String> selectedIds;
  final bool searchEnabled;
  final bool showCheckboxes;
  final bool showRadios;
  final String? warningBanner;
  final Widget? empty;
  final ValueChanged<Set<String>>? onConfirm;
  final bool popOnSelect;

  @override
  Widget build(BuildContext context) {
    return AppCatalogSelectPage(
      title: title,
      multiSelect: multiSelect || showCheckboxes,
      selectedIds: selectedIds,
      searchEnabled: searchEnabled,
      warningBanner: warningBanner,
      empty: empty,
      onConfirm: onConfirm,
      popOnSelect: popOnSelect,
      items: [for (final i in items) i.toCatalogItem()],
    );
  }
}
