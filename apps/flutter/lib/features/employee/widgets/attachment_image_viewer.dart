import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/employee/widgets/attachment_preview_kinds.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-screen attachment preview: image / text / PDF stub (L05/L07).
class AttachmentViewerPage extends StatefulWidget {
  const AttachmentViewerPage({
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

  /// Opens viewer when content is image, text, or PDF; no-op otherwise.
  static Future<void> openIfPreviewable(
    BuildContext context, {
    required String projectId,
    required String attachmentId,
    required String title,
    String? contentType,
    Future<Uint8List> Function()? loadBytes,
  }) async {
    if (!attachmentCanPreview(contentType)) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AttachmentViewerPage(
          projectId: projectId,
          attachmentId: attachmentId,
          title: title,
          contentType: contentType,
          loadBytes: loadBytes,
        ),
      ),
    );
  }

  /// Backward-compatible alias (opens any previewable type).
  static Future<void> openIfImage(
    BuildContext context, {
    required String projectId,
    required String attachmentId,
    required String title,
    String? contentType,
    Future<Uint8List> Function()? loadBytes,
  }) {
    return openIfPreviewable(
      context,
      projectId: projectId,
      attachmentId: attachmentId,
      title: title,
      contentType: contentType,
      loadBytes: loadBytes,
    );
  }

  @override
  State<AttachmentViewerPage> createState() => _AttachmentViewerPageState();
}

/// Deprecated name kept for existing imports/tests.
typedef AttachmentImageViewerPage = AttachmentViewerPage;

class _AttachmentViewerPageState extends State<AttachmentViewerPage> {
  static const _maxTextPreviewChars = 200000;

  Uint8List? _bytes;
  bool _loading = true;
  Object? _error;

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
        _error = e;
        _loading = false;
      });
    }
  }

  String _decodeText(Uint8List bytes) {
    try {
      return utf8.decode(bytes);
    } on FormatException {
      return latin1.decode(bytes, allowInvalid: true);
    }
  }

  Widget _body() {
    final l10n = AppLocalizations.of(context);
    if (_loading) return const CircularProgressIndicator();
    if (_error != null) {
      return Padding(
        padding: EdgeInsets.all(24),
        child: Text(AppErrors.localize(context, _error!), textAlign: TextAlign.center),
      );
    }
    final bytes = _bytes;
    if (bytes == null) return Text(l10n.projectNoAttachmentData);

    if (attachmentIsImageContentType(widget.contentType)) {
      return InteractiveViewer(
        minScale: 0.5,
        maxScale: 5,
        child: Image.memory(
          bytes,
          fit: BoxFit.contain,
          errorBuilder: (_, __, ___) => const Icon(Icons.broken_image_outlined, size: 64),
        ),
      );
    }

    if (attachmentIsTextContentType(widget.contentType)) {
      var text = _decodeText(bytes);
      var truncated = false;
      if (text.length > _maxTextPreviewChars) {
        text = text.substring(0, _maxTextPreviewChars);
        truncated = true;
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (truncated)
            MaterialBanner(
              content: Text(l10n.projectPreviewTruncated),
              actions: const [SizedBox.shrink()],
            ),
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: SelectableText(
                text,
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      fontFamily: 'monospace',
                      height: 1.35,
                    ),
              ),
            ),
          ),
        ],
      );
    }

    if (attachmentIsPdfContentType(widget.contentType)) {
      return Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.picture_as_pdf_outlined, size: 64, color: context.appColors.primary),
            const SizedBox(height: 16),
            Text(widget.title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(
              l10n.projectPdfPreviewUnavailable(
                '${bytes.length}',
                widget.contentType ?? 'application/pdf',
              ),
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.all(24),
      child: Text(
        l10n.projectNoPreviewForType(
          widget.contentType ?? l10n.commonUnknown,
          '${bytes.length}',
        ),
        textAlign: TextAlign.center,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final fillBody =
        attachmentIsTextContentType(widget.contentType) || attachmentIsPdfContentType(widget.contentType);
    return Scaffold(
      backgroundColor: context.appColors.surface,
      appBar: AppBar(
        title: Text(widget.title, overflow: TextOverflow.ellipsis),
      ),
      body: fillBody ? _body() : Center(child: _body()),
    );
  }
}
