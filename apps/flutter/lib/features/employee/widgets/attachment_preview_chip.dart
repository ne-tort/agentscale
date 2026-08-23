import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/employee/widgets/attachment_image_viewer.dart';
import 'package:prodavan/features/employee/widgets/attachment_preview_kinds.dart';

export 'package:prodavan/features/employee/widgets/attachment_preview_kinds.dart';

/// Small square image preview for inbox / chat attachments (L05/L07).
class AttachmentThumbnail extends StatefulWidget {
  const AttachmentThumbnail({
    super.key,
    required this.projectId,
    required this.attachmentId,
    this.contentType,
    this.size = 40,
    this.loadBytes,
    this.onTap,
  });

  final String projectId;
  final String attachmentId;
  final String? contentType;
  final double size;
  final Future<Uint8List> Function()? loadBytes;
  final VoidCallback? onTap;

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
    Widget child;
    if (_bytes != null) {
      child = ClipRRect(
        borderRadius: BorderRadius.circular(4),
        child: Image.memory(
          _bytes!,
          width: widget.size,
          height: widget.size,
          fit: BoxFit.cover,
          errorBuilder: (_, __, ___) => Icon(Icons.broken_image_outlined, size: widget.size * 0.5),
        ),
      );
    } else if (_loading) {
      child = SizedBox(
        width: widget.size,
        height: widget.size,
        child: const Center(child: CircularProgressIndicator(strokeWidth: 2)),
      );
    } else {
      child = Icon(attachmentPreviewIcon(widget.contentType), size: widget.size * 0.55);
    }

    if (widget.onTap == null) return child;
    return InkWell(
      onTap: widget.onTap,
      borderRadius: BorderRadius.circular(4),
      child: child,
    );
  }
}

/// Compact attachment chip with optional image thumbnail (L05/L07).
class AttachmentPreviewChip extends StatelessWidget {
  const AttachmentPreviewChip({
    super.key,
    required this.projectId,
    required this.attachmentId,
    required this.label,
    this.contentType,
    this.loadBytes,
    this.onOpen,
  });

  final String projectId;
  final String attachmentId;
  final String label;
  final String? contentType;
  final Future<Uint8List> Function()? loadBytes;
  final VoidCallback? onOpen;

  static bool isImageContentType(String? contentType) =>
      attachmentIsImageContentType(contentType);

  static bool canPreview(String? contentType) => attachmentCanPreview(contentType);

  Future<void> _defaultOpen(BuildContext context) {
    return AttachmentViewerPage.openIfPreviewable(
      context,
      projectId: projectId,
      attachmentId: attachmentId,
      title: label,
      contentType: contentType,
      loadBytes: loadBytes,
    );
  }

  @override
  Widget build(BuildContext context) {
    final open = onOpen ??
        (attachmentCanPreview(contentType) ? () => _defaultOpen(context) : null);

    Widget? leading;
    if (attachmentIsImageContentType(contentType)) {
      leading = SizedBox(
        width: 28,
        height: 28,
        child: AttachmentThumbnail(
          projectId: projectId,
          attachmentId: attachmentId,
          contentType: contentType,
          size: 28,
          loadBytes: loadBytes,
        ),
      );
    } else {
      leading = Icon(attachmentPreviewIcon(contentType), size: 14);
    }

    if (open == null) {
      return Chip(
        visualDensity: VisualDensity.compact,
        avatar: leading,
        label: Text(label, overflow: TextOverflow.ellipsis),
      );
    }

    return ActionChip(
      visualDensity: VisualDensity.compact,
      avatar: leading,
      label: Text(label, overflow: TextOverflow.ellipsis),
      onPressed: open,
    );
  }
}
