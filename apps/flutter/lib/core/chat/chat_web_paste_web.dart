import 'dart:js_interop';

import 'package:web/web.dart' as web;

import 'chat_clipboard.dart';

/// DOM `paste` listener for the chat composer (web build).
///
/// The paste event carries `clipboardData.files` for image/file pastes
/// (screenshots, copied files, browser «copy image») without any
/// clipboard-read permission prompt. Text pastes keep the native
/// TextField behavior — we never call preventDefault.
class WebChatPasteListener {
  web.EventListener? _listener;

  void start(void Function(List<DroppedChatFile>) onFiles) {
    stop();
    _listener = ((web.Event raw) {
      final event = raw as web.ClipboardEvent;
      final files = event.clipboardData?.files;
      final count = files?.length ?? 0;
      if (count == 0) return;
      _readFiles(files!, count, onFiles);
    }).toJS;
    web.document.addEventListener('paste', _listener);
  }

  Future<void> _readFiles(
    web.FileList files,
    int count,
    void Function(List<DroppedChatFile>) onFiles,
  ) async {
    final out = <DroppedChatFile>[];
    for (var i = 0; i < count; i++) {
      final file = files.item(i);
      if (file == null) continue;
      try {
        final buffer = await file.arrayBuffer().toDart;
        final bytes = buffer.toDart.asUint8List();
        if (bytes.isEmpty) continue;
        final name = file.name.trim();
        out.add(
          DroppedChatFile(
            name: name.isEmpty ? 'pasted-file-$i' : name,
            bytes: bytes,
          ),
        );
      } catch (_) {
        // Unreadable entry — skip, keep the readable ones.
      }
    }
    if (out.isNotEmpty) onFiles(out);
  }

  void stop() {
    if (_listener == null) return;
    web.document.removeEventListener('paste', _listener);
    _listener = null;
  }
}
