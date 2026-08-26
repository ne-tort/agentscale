import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/theme/app_palette.dart';

/// Persisted locale + appearance + auto-refresh interval.
class AppSettingsController extends ChangeNotifier {
  static const _keyLocale = 'prodavan.settings.locale';
  static const _keyTheme = 'prodavan.settings.theme';
  static const _keyAutoRefresh = 'prodavan.settings.auto_refresh_seconds';

  Locale _locale = const Locale('ru');
  AppThemeMode _themeMode = AppThemeMode.light;
  int _autoRefreshSeconds = kAppAutoRefreshDefaultSeconds;
  bool _loaded = false;

  Locale get locale => _locale;
  AppThemeMode get themeMode => _themeMode;

  /// Seconds between silent UI refreshes. `0` = off.
  int get autoRefreshSeconds => _autoRefreshSeconds;
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
    final refresh = prefs.getInt(_keyAutoRefresh);
    if (refresh != null && kAppAutoRefreshChoicesSeconds.contains(refresh)) {
      _autoRefreshSeconds = refresh;
    } else {
      _autoRefreshSeconds = kAppAutoRefreshDefaultSeconds;
    }
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

  Future<void> setAutoRefreshSeconds(int seconds) async {
    if (!kAppAutoRefreshChoicesSeconds.contains(seconds)) return;
    if (_autoRefreshSeconds == seconds) return;
    _autoRefreshSeconds = seconds;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_keyAutoRefresh, seconds);
  }
}

final appSettings = AppSettingsController();
