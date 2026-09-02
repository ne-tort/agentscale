import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

typedef ChatComposerSend = void Function(String text, List<String> attachmentRefs);

class _PendingAttachment {
  const _PendingAttachment({required this.id, required this.filename});

  final String id;
  final String filename;
}

class ChatComposer extends StatefulWidget {
  const ChatComposer({
    super.key,
    required this.onSend,
    this.enabled = true,
    this.disabledHint,
    this.onCancel,
    this.projectId,
    this.api,
  });

  final ChatComposerSend onSend;
  final bool enabled;
  final String? disabledHint;
  final VoidCallback? onCancel;
  final String? projectId;
  final ProdavanApi? api;

  @override
  State<ChatComposer> createState() => _ChatComposerState();
}

class _ChatComposerState extends State<ChatComposer> {
  final _controller = TextEditingController();
  final List<_PendingAttachment> _attachments = [];
  bool _uploading = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  bool get _canSend =>
      widget.enabled &&
      !_uploading &&
      (_controller.text.trim().isNotEmpty || _attachments.isNotEmpty);

  void _submit() {
    if (!_canSend) return;
    widget.onSend(
      _controller.text,
      _attachments.map((a) => a.id).toList(),
    );
    _controller.clear();
    setState(_attachments.clear);
  }

  Future<void> _pickFile() async {
    final projectId = widget.projectId;
    final api = widget.api;
    if (!widget.enabled || projectId == null || api == null || _uploading) return;

    final result = await FilePicker.platform.pickFiles(withData: true);
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    final bytes = file.bytes;
    if (bytes == null) return;
    if (!mounted) return;

    setState(() => _uploading = true);
    try {
      final body = await api.uploadProjectAttachment(
        projectId: projectId,
        filename: file.name,
        bytes: bytes,
      );
      final id = body['id'] as String? ?? body['storage_ref'] as String? ?? '';
      if (id.isEmpty) return;
      setState(() {
        _attachments.add(_PendingAttachment(id: id, filename: file.name));
      });
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('$e')),
        );
      }
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final canAttach = widget.enabled && widget.projectId != null && widget.api != null;
    return SafeArea(
      top: false,
      child: Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_attachments.isNotEmpty)
              Padding(
                padding: EdgeInsets.only(bottom: AppSpacing.sm),
                child: Wrap(
                  spacing: AppSpacing.xs,
                  runSpacing: AppSpacing.xs,
                  children: [
                    for (final att in _attachments)
                      InputChip(
                        label: Text(att.filename, overflow: TextOverflow.ellipsis),
                        onDeleted: widget.enabled
                            ? () => setState(() => _attachments.remove(att))
                            : null,
                      ),
                  ],
                ),
              ),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                if (canAttach)
                  IconButton(
                    tooltip: l10n.projectAttachmentFallback,
                    onPressed: _uploading ? null : _pickFile,
                    icon: _uploading
                        ? SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.attach_file),
                  ),
                Expanded(
                  child: TextField(
                    controller: _controller,
                    enabled: widget.enabled && !_uploading,
                    minLines: 1,
                    maxLines: 6,
                    decoration: InputDecoration(
                      hintText: widget.enabled
                          ? l10n.projectMessageHint
                          : (widget.disabledHint ?? l10n.projectMessageHint),
                      border: const OutlineInputBorder(),
                    ),
                    onSubmitted: widget.enabled ? (_) => _submit() : null,
                  ),
                ),
                SizedBox(width: AppSpacing.sm),
                if (widget.onCancel != null)
                  IconButton(
                    tooltip: l10n.commonCancel,
                    onPressed: widget.onCancel,
                    icon: const Icon(Icons.stop_circle_outlined),
                  )
                else
                  IconButton(
                    tooltip: l10n.commonContinueAction,
                    onPressed: _canSend ? _submit : null,
                    icon: const Icon(Icons.send),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
