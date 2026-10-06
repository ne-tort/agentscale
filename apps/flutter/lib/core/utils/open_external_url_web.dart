// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;

/// Web: открыть внешний URL (OAuth-ссылка) в новой вкладке браузера.
Future<bool> openExternalUrl(String url) async {
  final anchor = html.AnchorElement(href: url)
    ..target = '_blank'
    ..rel = 'noopener noreferrer'
    ..style.display = 'none';
  html.document.body?.append(anchor);
  anchor.click();
  anchor.remove();
  return true;
}
