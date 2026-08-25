import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_checkbox.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_radio.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_selector_page.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Demo of core primitives without backend (L02).
class CoreGalleryPage extends StatelessWidget {
  const CoreGalleryPage({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = [
      AppEntityRow(
        id: '1',
        title: l10n.galleryAlpha,
        subtitle: l10n.galleryDemo,
        cells: {'status': 'active', 'count': '3'},
      ),
      AppEntityRow(
        id: '2',
        title: l10n.galleryBeta,
        subtitle: l10n.galleryDemo,
        cells: {'status': 'paused', 'count': '0'},
      ),
    ];

    return AppScaffold(
      title: Text(l10n.galleryCoreGallery),
      body: ListView(
        padding: EdgeInsets.all(AppSpacing.md),
        children: [
          Text(l10n.galleryButtons, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              AppIconButton(
                icon: Icons.add,
                tooltip: l10n.commonAdd,
                onPressed: () {},
              ),
              AppIconToggle(
                icon: Icons.filter_list,
                tooltip: l10n.commonFilter,
                selected: true,
                onPressed: () {},
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: AppButton(
                  label: l10n.commonCreate,
                  expanded: false,
                  onPressed: () {},
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(l10n.galleryListItem, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          AppListItem(
            title: Text(l10n.gallerySampleRow),
            subtitle: Text(l10n.gallerySubtitle),
            trailing: Icon(Icons.chevron_right),
            onTap: () {},
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(l10n.gallerySelection, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          AppCheckbox(value: true, onChanged: null, label: l10n.galleryCheck),
          AppRadio<int>(value: 1, groupValue: 1, onChanged: null, label: l10n.galleryRadio),
          const SizedBox(height: AppSpacing.lg),
          AppButton(
            label: l10n.gallerySelector,
            onPressed: () async {
              await Navigator.of(context).push<Set<String>>(
                MaterialPageRoute(
                  builder: (_) => AppSelectorPage(
                    title: l10n.galleryPick,
                    multiSelect: true,
                    showCheckboxes: true,
                    searchEnabled: true,
                    items: [
                      AppSelectorItem(id: 'a', title: l10n.galleryOne),
                      AppSelectorItem(id: 'b', title: l10n.galleryTwo),
                    ],
                  ),
                ),
              );
            },
          ),
          const SizedBox(height: AppSpacing.sm),
          AppButton(
            label: l10n.galleryDanger,
            variant: AppButtonVariant.outlined,
            onPressed: () {
              DangerConfirmPage.push(
                context,
                title: l10n.commonDelete,
                message: l10n.galleryDemoConfirm,
              );
            },
          ),
          const SizedBox(height: AppSpacing.lg),
          Text(l10n.settings, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          AppSwitchPreference(
            title: l10n.settingsThemeLight,
            icon: Icons.light_mode_outlined,
            value: true,
            onChanged: (_) async {},
          ),
          AppChoicePreference<String>(
            title: l10n.settingsLanguage,
            icon: Icons.translate,
            value: 'ru',
            choices: const ['ru', 'en'],
            keyFor: (v) => v,
            labelFor: (v) => v == 'ru' ? l10n.settingsLanguageRu : l10n.settingsLanguageEn,
            onSave: (_) async {},
          ),
          const SizedBox(height: AppSpacing.lg),
          Text('EmptyPlaceholder', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          EmptyPlaceholder(title: l10n.commonEmpty),
          const SizedBox(height: AppSpacing.lg),
          Text(l10n.galleryEntityCollection, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          SizedBox(
            height: 280,
            child: AppEntityCollection(
              rows: rows,
              primaryColumnLabel: l10n.commonName,
              columns: [
                AppEntityColumn(id: 'status', label: l10n.commonStatus, width: 96),
                AppEntityColumn(
                  id: 'count',
                  label: l10n.galleryCount,
                  width: 64,
                  align: AppEntityColumnAlign.end,
                ),
              ],
              onOpen: (_) {},
              toolbar: [
                AppIconButton(
                  icon: Icons.add,
                  tooltip: l10n.commonAdd,
                  onPressed: () {},
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
