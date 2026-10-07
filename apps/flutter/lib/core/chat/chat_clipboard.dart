import 'dart:io';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/services.dart';
import 'package:pasteboard/pasteboard.dart';

/// A file captured outside the app: OS drag & drop onto the chat area or a
/// clipboard paste (Ctrl+V of files / an image).
class DroppedChatFile {
  const DroppedChatFile({required this.name, required this.bytes});

  final String name;
  final Uint8List bytes;
}

/// Reads non-text clipboard content for the chat composer.
///
/// Plain-text paste is NOT intercepted — the TextField handles it natively.
/// This reader surfaces files (Explorer Ctrl+C) and images (screenshots,
/// browser «copy image») which the default paste silently drops.
abstract class ChatClipboardReader {
  Future<List<DroppedChatFile>> readFilesOrImage();
}

/// [pasteboard]-backed default: files first (a copied-file clipboard carries
/// no image bytes), then a standalone image. Unsupported platforms return
/// an empty list — paste then behaves exactly as before.
class PasteboardClipboardReader implements ChatClipboardReader {
  const PasteboardClipboardReader();

  @override
  Future<List<DroppedChatFile>> readFilesOrImage() async {
    // Web: pasteboard's image getter calls navigator.clipboard.read() —
    // a browser permission prompt on EVERY paste (even text-only
    // clipboards). The web build uses the DOM paste listener instead.
    if (kIsWeb) return const [];
    try {
      final paths = await Pasteboard.files();
      if (paths.isNotEmpty) {
        final out = <DroppedChatFile>[];
        for (final path in paths) {
          final file = File(path);
          if (!await file.exists()) continue;
          final bytes = await file.readAsBytes();
          if (bytes.isEmpty) continue;
          final name = file.uri.pathSegments.isNotEmpty
              ? file.uri.pathSegments.last
              : path;
          out.add(DroppedChatFile(name: name.isEmpty ? 'pasted-file' : name, bytes: bytes));
        }
        if (out.isNotEmpty) return out;
      }
    } catch (_) {
      // Files unavailable on this platform → try the image path.
    }
    try {
      final image = await Pasteboard.image;
      if (image != null && image.isNotEmpty) {
        final stamp = DateTime.now().millisecondsSinceEpoch;
        return [DroppedChatFile(name: 'pasted-image-$stamp.png', bytes: image)];
      }
    } catch (_) {
      // No image either → plain text paste.
    }
    return const [];
  }
}
