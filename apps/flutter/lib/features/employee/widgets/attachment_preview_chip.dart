import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';

bool attachmentIsImageContentType(String? contentType) {
  return contentType != null && contentType.startsWith('image/');
}

/// Small square image preview for inbox / chat attachments (L05/L07).
class AttachmentThumbnail extends StatefulWidget {
  const AttachmentThumbnail({
    super.key,
    required this.projectId,
    required this.attachmentId,
    this.contentType,
    this.size = 40,
    this.loadBytes,
  });

  final String projectId;
  final String attachmentId;
  final String? contentType;
  final double size;
  final Future<Uint8List> Function()? loadBytes;

  @override
  State<AttachmentThumbnail> createState() => _AttachmentThumbnailState();
}

class _AttachmentThumbnailState extends State<AttachmentThumbnail> {
  Uint8List? _bytes;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    if (attachmentIsImageContentType(widget.contentType)) {
      _loadPreview();
    }
  }

  @override
  void didUpdateWidget(covariant AttachmentThumbnail oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.attachmentId != oldWidget.attachmentId ||
        widget.contentType != oldWidget.contentType) {
      _bytes = null;
      if (attachmentIsImageContentType(widget.contentType)) {
        _loadPreview();
      }
    }
  }

  Future<void> _loadPreview() async {
    if (_loading || _bytes != null) return;
    setState(() => _loading = true);
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
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_bytes != null) {
      return ClipRRect(
        borderRadius: BorderRadius.circular(4),
        child: Image.memory(
          _bytes!,
          width: widget.size,
          height: widget.size,
          fit: BoxFit.cover,
          errorBuilder: (_, __, ___) => Icon(Icons.broken_image_outlined, size: widget.size * 0.5),
        ),
      );
    }
    if (_loading) {
      return SizedBox(
        width: widget.size,
        height: widget.size,
        child: const Center(child: CircularProgressIndicator(strokeWidth: 2)),
      );
    }
    return Icon(Icons.insert_drive_file_outlined, size: widget.size * 0.55);
  }
}

/// Compact attachment chip with optional image thumbnail (L05/L07).
class AttachmentPreviewChip extends StatefulWidget {
  const AttachmentPreviewChip({
    super.key,
    required this.projectId,
    required this.attachmentId,
    required this.label,
    this.contentType,
    this.loadBytes,
  });

  final String projectId;
  final String attachmentId;
  final String label;
  final String? contentType;
  final Future<Uint8List> Function()? loadBytes;

  static bool isImageContentType(String? contentType) =>
      attachmentIsImageContentType(contentType);

  @override
  State<AttachmentPreviewChip> createState() => _AttachmentPreviewChipState();
}

class _AttachmentPreviewChipState extends State<AttachmentPreviewChip> {
  Widget? _leading() {
    if (!AttachmentPreviewChip.isImageContentType(widget.contentType)) {
      return const Icon(Icons.attach_file, size: 14);
    }
    return SizedBox(
      width: 28,
      height: 28,
      child: AttachmentThumbnail(
        projectId: widget.projectId,
        attachmentId: widget.attachmentId,
        contentType: widget.contentType,
        size: 28,
        loadBytes: widget.loadBytes,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Chip(
      visualDensity: VisualDensity.compact,
      avatar: _leading(),
      label: Text(
        widget.label,
        overflow: TextOverflow.ellipsis,
      ),
    );
  }
}
