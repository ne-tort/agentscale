import 'package:flutter/material.dart';

import 'package:prodavan/core/settings/app_settings_controller.dart';
import 'package:prodavan/core/theme/app_palette.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_selector_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Language + appearance (page, not dialog).
class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  Future<void> _pickLocale(BuildContext context) async {
    final l10n = AppLocalizations.of(context);
    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppSelectorPage(
          title: l10n.settingsLanguage,
          showRadios: true,
          selectedIds: {appSettings.locale.languageCode},
          items: [
            AppSelectorItem(id: 'ru', title: l10n.settingsLanguageRu),
            AppSelectorItem(id: 'en', title: l10n.settingsLanguageEn),
          ],
        ),
      ),
    );
    if (picked != null && picked.isNotEmpty) {
      await appSettings.setLocale(Locale(picked.first));
    }
  }

  Future<void> _pickTheme(BuildContext context) async {
    final l10n = AppLocalizations.of(context);
    final current = switch (appSettings.themeMode) {
      AppThemeMode.light => 'light',
      AppThemeMode.dark => 'dark',
      AppThemeMode.ultraDark => 'ultraDark',
    };
    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppSelectorPage(
          title: l10n.settingsTheme,
          showRadios: true,
          selectedIds: {current},
          items: [
            AppSelectorItem(id: 'light', title: l10n.settingsThemeLight),
            AppSelectorItem(id: 'dark', title: l10n.settingsThemeDark),
            AppSelectorItem(id: 'ultraDark', title: l10n.settingsThemeUltraDark),
          ],
        ),
      ),
    );
    if (picked != null && picked.isNotEmpty) {
      final mode = switch (picked.first) {
        'dark' => AppThemeMode.dark,
        'ultraDark' => AppThemeMode.ultraDark,
        _ => AppThemeMode.light,
      };
      await appSettings.setThemeMode(mode);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: appSettings,
      builder: (context, _) {
        final l10n = AppLocalizations.of(context);
        return AppScaffold(
          title: Text(l10n.settings),
          body: ListView(
            padding: const EdgeInsets.all(AppSpacing.lg),
            children: [
              AppSectionHeader(title: l10n.settingsLanguage),
              ListTile(
                title: Text(l10n.settingsLanguage),
                subtitle: Text(
                  appSettings.locale.languageCode == 'ru'
                      ? l10n.settingsLanguageRu
                      : l10n.settingsLanguageEn,
                ),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _pickLocale(context),
              ),
              const SizedBox(height: AppSpacing.lg),
              AppSectionHeader(title: l10n.settingsTheme),
              ListTile(
                title: Text(l10n.settingsTheme),
                subtitle: Text(switch (appSettings.themeMode) {
                  AppThemeMode.light => l10n.settingsThemeLight,
                  AppThemeMode.dark => l10n.settingsThemeDark,
                  AppThemeMode.ultraDark => l10n.settingsThemeUltraDark,
                }),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _pickTheme(context),
              ),
            ],
          ),
        );
      },
    );
  }
}
