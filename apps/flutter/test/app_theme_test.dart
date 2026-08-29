import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_palette.dart';
import 'package:prodavan/core/theme/app_theme.dart';

void main() {
  test('loginPage light uses gray background and white card', () {
    final base = AppTheme.light;
    final login = AppTheme.loginPage(base, AppThemeMode.light);

    expect(login.scaffoldBackgroundColor, AppPalette.light.surfaceContainer);
    expect(login.cardTheme.color, AppPalette.light.surface);
    expect(base.scaffoldBackgroundColor, isNot(login.scaffoldBackgroundColor));
  });

  test('loginPage leaves dark and ultraDark unchanged', () {
    for (final mode in [AppThemeMode.dark, AppThemeMode.ultraDark]) {
      final base = AppTheme.forMode(mode);
      expect(identical(AppTheme.loginPage(base, mode), base), isTrue);
    }
  });
}
