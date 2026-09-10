import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/sign_out.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/settings/app_settings_controller.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_palette.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Language + appearance + auto-refresh.
class SettingsPage extends StatelessWidget {
  const SettingsPage({
    super.key,
    this.embedded = false,
    this.leadingChildren = const [],
    this.showSignOut = true,
  });

  /// When true, render inside shell [IndexedStack] without app bar chrome.
  final bool embedded;

  /// Widgets inserted before language/theme (e.g. company credentials).
  final List<Widget> leadingChildren;

  /// Hide sign-out on login / offline gate (session may still look authenticated).
  final bool showSignOut;

  static const _locales = ['ru', 'en'];
  static const _themes = ['light', 'dark', 'ultraDark'];

  String _refreshLabel(AppLocalizations l10n, int seconds) {
    if (seconds <= 0) return l10n.settingsRefreshOff;
    if (seconds < 60) return l10n.settingsRefreshSeconds('$seconds');
    final minutes = seconds ~/ 60;
    return l10n.settingsRefreshMinutes('$minutes');
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([appSettings, tokenSession]),
      builder: (context, _) {
        final l10n = AppLocalizations.of(context);
        final themeKey = switch (appSettings.themeMode) {
          AppThemeMode.light => 'light',
          AppThemeMode.dark => 'dark',
          AppThemeMode.ultraDark => 'ultraDark',
        };
        final body = ListView(
          padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
          children: [
            ...leadingChildren,
            AppChoicePreference<String>(
              title: l10n.settingsLanguage,
              icon: Icons.translate,
              value: appSettings.locale.languageCode,
              choices: _locales,
              keyFor: (v) => v,
              labelFor: (v) =>
                  v == 'ru' ? l10n.settingsLanguageRu : l10n.settingsLanguageEn,
              iconFor: (v) => v == 'ru' ? Icons.translate : Icons.language,
              onSave: (v) async => appSettings.setLocale(Locale(v)),
            ),
            AppChoicePreference<String>(
              title: l10n.settingsTheme,
              icon: Icons.palette_outlined,
              value: themeKey,
              choices: _themes,
              keyFor: (v) => v,
              labelFor: (v) => switch (v) {
                'dark' => l10n.settingsThemeDark,
                'ultraDark' => l10n.settingsThemeUltraDark,
                _ => l10n.settingsThemeLight,
              },
              iconFor: (v) => switch (v) {
                'dark' => Icons.dark_mode_outlined,
                'ultraDark' => Icons.contrast,
                _ => Icons.light_mode_outlined,
              },
              onSave: (v) async {
                final mode = switch (v) {
                  'dark' => AppThemeMode.dark,
                  'ultraDark' => AppThemeMode.ultraDark,
                  _ => AppThemeMode.light,
                };
                await appSettings.setThemeMode(mode);
              },
            ),
            AppChoicePreference<int>(
              title: l10n.settingsRefresh,
              icon: Icons.update_rounded,
              value: appSettings.autoRefreshSeconds,
              choices: kAppAutoRefreshChoicesSeconds,
              keyFor: (v) => '$v',
              labelFor: (v) => _refreshLabel(l10n, v),
              presentValue: (v) => _refreshLabel(l10n, v),
              onSave: (v) async => appSettings.setAutoRefreshSeconds(v),
            ),
            if (showSignOut && tokenSession.isAuthenticated)
              AppNavPreference(
                title: l10n.authSignOut,
                icon: Icons.logout_rounded,
                accentColor: context.appColors.warning,
                onTap: () => signOut(context),
              ),
          ],
        );
        if (embedded) return body;
        return AppScaffold(
          title: Text(l10n.settings),
          body: body,
        );
      },
    );
  }
}
