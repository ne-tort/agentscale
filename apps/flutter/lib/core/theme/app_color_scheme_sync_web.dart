import 'dart:js_interop';

import 'package:prodavan/core/theme/app_palette.dart';

@JS('document.querySelector')
external JSObject? _querySelector(JSString selectors);

extension type _MetaElement(JSObject _) implements JSObject {
  external void setAttribute(JSString name, JSString value);
}

/// Sets `<meta name="color-scheme">` so browser-drawn controls (password
/// manager key icon, caret, autofill background) match the app theme.
void syncDocumentColorScheme(AppThemeMode mode) {
  final meta = _querySelector('meta[name="color-scheme"]'.toJS);
  if (meta == null) return;
  _MetaElement(meta).setAttribute(
    'content'.toJS,
    switch (mode) {
      AppThemeMode.light => 'light',
      AppThemeMode.dark || AppThemeMode.ultraDark => 'dark',
    }.toJS,
  );
}
