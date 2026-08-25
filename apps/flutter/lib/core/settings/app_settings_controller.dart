import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:prodavan/core/theme/app_palette.dart';

/// Persisted locale + appearance.
class AppSettingsController extends ChangeNotifier {
  static const _keyLocale = 'prodavan.settings.locale';
  static const _keyTheme = 'prodavan.settings.theme';

  Locale _locale = const Locale('ru');
  AppThemeMode _themeMode = AppThemeMode.light;
  bool _loaded = false;

  Locale get locale => _locale;
  AppThemeMode get themeMode => _themeMode;
  bool get isLoaded => _loaded;

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final loc = prefs.getString(_keyLocale);
    if (loc == 'en' || loc == 'ru') {
      _locale = Locale(loc!);
    } else {
      _locale = const Locale('ru');
    }
    final theme = prefs.getString(_keyTheme);
    _themeMode = switch (theme) {
      'dark' => AppThemeMode.dark,
      'ultraDark' => AppThemeMode.ultraDark,
      _ => AppThemeMode.light,
    };
    _loaded = true;
    notifyListeners();
  }

  Future<void> setLocale(Locale locale) async {
    if (locale.languageCode != 'en' && locale.languageCode != 'ru') return;
    _locale = Locale(locale.languageCode);
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyLocale, _locale.languageCode);
  }

  Future<void> setThemeMode(AppThemeMode mode) async {
    _themeMode = mode;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _keyTheme,
      switch (mode) {
        AppThemeMode.light => 'light',
        AppThemeMode.dark => 'dark',
        AppThemeMode.ultraDark => 'ultraDark',
      },
    );
  }
}

final appSettings = AppSettingsController();
