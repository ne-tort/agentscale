import 'package:flutter/material.dart';

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
import 'package:prodavan/core/widgets/empty_state.dart';

/// Demo of core primitives without backend (L02).
class CoreGalleryPage extends StatelessWidget {
  const CoreGalleryPage({super.key});

  @override
  Widget build(BuildContext context) {
    final rows = [
      const AppEntityRow(
        id: '1',
        title: 'Alpha',
        subtitle: 'demo',
        cells: {'status': 'active', 'count': '3'},
      ),
      const AppEntityRow(
        id: '2',
        title: 'Beta',
        subtitle: 'demo',
        cells: {'status': 'paused', 'count': '0'},
      ),
    ];

    return AppScaffold(
      title: const Text('Core gallery'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          Text('Buttons', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              AppIconButton(
                icon: Icons.add,
                tooltip: 'Add',
                onPressed: () {},
              ),
              AppIconToggle(
                icon: Icons.filter_list,
                tooltip: 'Filter',
                selected: true,
                onPressed: () {},
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: AppButton(
                  label: 'Создать',
                  expanded: false,
                  onPressed: () {},
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          Text('List item', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          AppListItem(
            title: const Text('Sample row'),
            subtitle: const Text('subtitle'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {},
          ),
          const SizedBox(height: AppSpacing.lg),
          Text('Selection', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          const AppCheckbox(value: true, onChanged: null, label: 'Check'),
          const AppRadio<int>(value: 1, groupValue: 1, onChanged: null, label: 'Radio'),
          const SizedBox(height: AppSpacing.lg),
          AppButton(
            label: 'Selector',
            onPressed: () async {
              await Navigator.of(context).push<Set<String>>(
                MaterialPageRoute(
                  builder: (_) => AppSelectorPage(
                    title: 'Pick',
                    multiSelect: true,
                    showCheckboxes: true,
                    searchEnabled: true,
                    items: const [
                      AppSelectorItem(id: 'a', title: 'One'),
                      AppSelectorItem(id: 'b', title: 'Two'),
                    ],
                  ),
                ),
              );
            },
          ),
          const SizedBox(height: AppSpacing.sm),
          AppButton(
            label: 'Danger',
            variant: AppButtonVariant.outlined,
            onPressed: () {
              DangerConfirmPage.push(
                context,
                title: 'Удалить',
                message: 'Demo confirm',
              );
            },
          ),
          const SizedBox(height: AppSpacing.lg),
          Text('EntityCollection', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          SizedBox(
            height: 280,
            child: AppEntityCollection(
              rows: rows,
              columns: const [
                AppEntityColumn(id: 'status', label: 'Status'),
                AppEntityColumn(id: 'count', label: 'Count'),
              ],
              onOpen: (_) {},
              toolbar: [
                AppIconButton(
                  icon: Icons.add,
                  tooltip: 'Add',
                  onPressed: () {},
                ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.lg),
          const EmptyState(title: 'Пусто', subtitle: null),
        ],
      ),
    );
  }
}
