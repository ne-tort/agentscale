import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/employee/widgets/attachment_preview_chip.dart';

/// Full-screen image viewer for project attachments (L05/L07).
class AttachmentImageViewerPage extends StatefulWidget {
  const AttachmentImageViewerPage({
    super.key,
    required this.projectId,
    required this.attachmentId,
    required this.title,
    this.contentType,
    this.loadBytes,
  });

  final String projectId;
  final String attachmentId;
  final String title;
  final String? contentType;
  final Future<Uint8List> Function()? loadBytes;

  /// Opens viewer when [contentType] is an image; no-op otherwise.
  static Future<void> openIfImage(
    BuildContext context, {
    required String projectId,
    required String attachmentId,
    required String title,
    String? contentType,
    Future<Uint8List> Function()? loadBytes,
  }) async {
    if (!attachmentIsImageContentType(contentType)) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AttachmentImageViewerPage(
          projectId: projectId,
          attachmentId: attachmentId,
          title: title,
          contentType: contentType,
          loadBytes: loadBytes,
        ),
      ),
    );
  }

  @override
  State<AttachmentImageViewerPage> createState() => _AttachmentImageViewerPageState();
}

class _AttachmentImageViewerPageState extends State<AttachmentImageViewerPage> {
  Uint8List? _bytes;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final bytes = widget.loadBytes != null
          ? await widget.loadBytes!()
          : await workContext.api.downloadProjectAttachmentBytes(
              projectId: widget.projectId,
              attachmentId: widget.attachmentId,
            );
      if (!mounted) return;
      setState(() {
        _bytes = bytes;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: scheme.surface,
      appBar: AppBar(
        title: Text(widget.title, overflow: TextOverflow.ellipsis),
      ),
      body: Center(
        child: _loading
            ? const CircularProgressIndicator()
            : _error != null
                ? Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(_error!, textAlign: TextAlign.center),
                  )
                : _bytes == null
                    ? const Text('No image data')
                    : InteractiveViewer(
                        minScale: 0.5,
                        maxScale: 5,
                        child: Image.memory(
                          _bytes!,
                          fit: BoxFit.contain,
                          errorBuilder: (_, __, ___) => const Icon(Icons.broken_image_outlined, size: 64),
                        ),
                      ),
      ),
    );
  }
}
