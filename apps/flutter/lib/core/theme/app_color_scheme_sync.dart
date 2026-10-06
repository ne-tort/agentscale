/// Keeps the browser's `color-scheme` in sync with the app theme mode.
///
/// Native input decorations (Chrome password-manager key icon, caret,
/// autofill background, scrollbars) follow the document color scheme, not
/// the Flutter theme — without this they render dark-on-dark in dark mode.
library;

export 'app_color_scheme_sync_stub.dart'
    if (dart.library.js_interop) 'app_color_scheme_sync_web.dart';
