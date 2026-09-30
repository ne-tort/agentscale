import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';

/// Native/desktop save dialog (file_picker `saveFile`).
Future<bool> saveWorkspaceFileBytes({
  required String filename,
  required Uint8List bytes,
}) async {
  final path = await FilePicker.platform.saveFile(
    fileName: filename,
    bytes: bytes,
  );
  return path != null;
}
