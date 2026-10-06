import 'dart:io';

/// Десктоп: открыть внешний URL (OAuth-ссылка) системным браузером.
Future<bool> openExternalUrl(String url) async {
  try {
    if (Platform.isWindows) {
      await Process.start('cmd', ['/c', 'start', '', url], runInShell: true);
    } else if (Platform.isMacOS) {
      await Process.start('open', [url]);
    } else {
      await Process.start('xdg-open', [url]);
    }
    return true;
  } catch (_) {
    return false;
  }
}
