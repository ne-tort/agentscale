// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'dart:typed_data';

/// Web save: browser-native download via a Blob + hidden anchor click.
///
/// `file_picker.saveFile` is unsupported on web (silently does nothing or
/// hangs) - this is what made module export buttons look "dead".
Future<bool> saveWorkspaceFileBytes({
  required String filename,
  required Uint8List bytes,
}) async {
  final blob = html.Blob([bytes]);
  final url = html.Url.createObjectUrlFromBlob(blob);
  final anchor = html.AnchorElement(href: url)
    ..download = filename
    ..style.display = 'none';
  html.document.body?.append(anchor);
  anchor.click();
  anchor.remove();
  html.Url.revokeObjectUrl(url);
  return true;
}
