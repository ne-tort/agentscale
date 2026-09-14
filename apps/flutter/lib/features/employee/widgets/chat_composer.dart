import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Wide limits aligned with API agent constraints (not unbounded).
const int kChatMaxMessageChars = 500000;
const int kChatMaxAttachmentsPerMessage = 32;
const int kChatMaxAttachmentBytesClient = 500 * 1024 * 1024; // platform ceiling; server enforces company policy

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
    this.streaming = false,
    this.disabledHint,
    this.onCancel,
    this.onOpenSettings,
    this.projectId,
    this.api,
    this.wakeMode = false,
    this.waking = false,
    this.onWake,
    this.updateMode = false,
    this.updating = false,
    this.onUpdate,
    this.onDismissUpdate,
  });

  final ChatComposerSend onSend;
  final bool enabled;
  final bool streaming;
  final String? disabledHint;
  final VoidCallback? onCancel;
  final VoidCallback? onOpenSettings;
  final String? projectId;
  final ProdavanApi? api;
  /// When true, field is not sendable but tappable — [onWake] resumes/reloads.
  final bool wakeMode;
  /// In-progress resume/reload — spinner on wake panel, ignore further taps.
  final bool waking;
  final VoidCallback? onWake;
  /// Workspace outdated — block input; tap updates, X dismisses the mark.
  final bool updateMode;
  final bool updating;
  final VoidCallback? onUpdate;
  final VoidCallback? onDismissUpdate;

  @override
  State<ChatComposer> createState() => _ChatComposerState();
}

class _ChatComposerState extends State<ChatComposer> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  final List<_PendingAttachment> _attachments = [];
  bool _uploading = false;
  bool _multiline = false;

  @override
  void initState() {
    super.initState();
    _controller.addListener(_onTextChanged);
  }

  @override
  void dispose() {
    _controller.removeListener(_onTextChanged);
    _focusNode.dispose();
    _controller.dispose();
    super.dispose();
  }

  bool get _canSend =>
      widget.enabled &&
      !widget.streaming &&
      !_uploading &&
      (_controller.text.trim().isNotEmpty || _attachments.isNotEmpty);

  bool _computeMultiline(BuildContext context) {
    final text = _controller.text;
    if (text.contains('\n')) return true;
    if (text.isEmpty) return false;
    final style = Theme.of(context).textTheme.bodyMedium ?? const TextStyle(fontSize: 16);
    final inset = AppSpacing.md * 2 + 120;
    final maxWidth = MediaQuery.sizeOf(context).width - inset;
    if (maxWidth <= 0) return false;
    final painter = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: Directionality.of(context),
      maxLines: null,
    )..layout(maxWidth: maxWidth);
    final lineHeight = style.fontSize! * (style.height ?? 1.2);
    return painter.height > lineHeight * 1.4;
  }

  void _onTextChanged() {
    if (!mounted) return;
    setState(() {});
  }

  void _submit() {
    if (!_canSend) return;
    final text = _controller.text;
    if (text.length > kChatMaxMessageChars) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Сообщение слишком длинное (макс. $kChatMaxMessageChars знаков)')),
      );
      return;
    }
    if (_attachments.length > kChatMaxAttachmentsPerMessage) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Слишком много вложений (макс. $kChatMaxAttachmentsPerMessage)')),
      );
      return;
    }
    widget.onSend(
      text,
      _attachments.map((a) => a.id).toList(),
    );
    _controller.clear();
    setState(_attachments.clear);
  }

  KeyEventResult _handleKeyEvent(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.ignored;
    if (event.logicalKey != LogicalKeyboardKey.enter) return KeyEventResult.ignored;
    if (HardwareKeyboard.instance.isShiftPressed) return KeyEventResult.ignored;
    if (!widget.enabled || widget.streaming) return KeyEventResult.handled;
    _submit();
    return KeyEventResult.handled;
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
    if (_attachments.length >= kChatMaxAttachmentsPerMessage) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Слишком много вложений (макс. $kChatMaxAttachmentsPerMessage)')),
      );
      return;
    }
    if (bytes.length > kChatMaxAttachmentBytesClient) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Файл слишком большой')),
      );
      return;
    }

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

  InputDecoration _fieldDecoration(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return kBorderlessInputDecoration.copyWith(
      hintText: widget.enabled
          ? l10n.projectMessageHint
          : (widget.disabledHint ?? l10n.projectMessageHint),
      filled: false,
      isDense: true,
      contentPadding: EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: AppSpacing.sm,
      ),
    );
  }

  Widget _attachButton(AppLocalizations l10n) {
    final canAttach = widget.enabled && widget.projectId != null && widget.api != null;
    if (!canAttach) return const SizedBox.shrink();
    return IconButton(
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
      tooltip: l10n.projectAttachmentFallback,
      onPressed: _uploading ? null : _pickFile,
      icon: _uploading
          ? const SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : const Icon(Icons.attach_file),
    );
  }

  Widget _plusButton(AppLocalizations l10n) {
    if (widget.onOpenSettings == null) return const SizedBox.shrink();
    return IconButton(
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
      tooltip: l10n.projectChatAddAction,
      onPressed: widget.enabled ? widget.onOpenSettings : null,
      icon: const Icon(Icons.add),
    );
  }

  Widget _sendButton(AppLocalizations l10n) {
    if (widget.onCancel != null) {
      return IconButton(
        visualDensity: VisualDensity.compact,
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
        tooltip: l10n.commonCancel,
        onPressed: widget.onCancel,
        icon: const Icon(Icons.stop_circle_outlined),
      );
    }
    return IconButton(
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
      tooltip: l10n.commonContinueAction,
      onPressed: _canSend ? _submit : null,
      icon: const Icon(Icons.send),
    );
  }

  /// Wake mode: not a TextField (I-beam) — clickable warning row + optional spinner.
  Widget _wakePanel(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final warning = context.appColors.warning;
    final label = widget.disabledHint ?? l10n.projectMessageHint;
    final canTap = !widget.waking && widget.onWake != null;
    return MouseRegion(
      cursor: canTap ? SystemMouseCursors.click : SystemMouseCursors.basic,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: canTap ? widget.onWake : null,
        child: Padding(
          padding: EdgeInsets.symmetric(
            horizontal: AppSpacing.sm,
            vertical: AppSpacing.sm,
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  label,
                  style: TextStyle(color: warning),
                ),
              ),
              if (widget.waking)
                SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: warning,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _textField(BuildContext context) {
    return Focus(
      onKeyEvent: _handleKeyEvent,
      child: TextField(
        controller: _controller,
        focusNode: _focusNode,
        enabled: !_uploading && widget.enabled,
        readOnly: !widget.enabled,
        minLines: 1,
        maxLines: 40,
        maxLength: kChatMaxMessageChars,
        buildCounter: (
          context, {
          required currentLength,
          required isFocused,
          maxLength,
        }) =>
            null,
        decoration: _fieldDecoration(context),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    final multiline = !widget.wakeMode && !widget.updateMode && _computeMultiline(context);

    if (multiline != _multiline) {
      _multiline = multiline;
    }

    final Widget field;
    if (widget.wakeMode) {
      field = _wakePanel(context);
    } else if (widget.updateMode) {
      field = _updatePanel(context);
    } else {
      field = _textField(context);
    }

    return SafeArea(
      top: false,
      child: Padding(
        padding: EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.sm, AppSpacing.md, AppSpacing.md),
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
                        onDeleted: widget.enabled && !widget.streaming
                            ? () => setState(() => _attachments.remove(att))
                            : null,
                      ),
                  ],
                ),
              ),
            Container(
              decoration: BoxDecoration(
                color: scheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(12),
              ),
              padding: EdgeInsets.symmetric(
                horizontal: AppSpacing.xs,
                vertical: AppSpacing.xs,
              ),
              child: (widget.wakeMode || widget.updateMode)
                  ? field
                  : multiline
                      ? Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            field,
                            Row(
                              children: [
                                _plusButton(l10n),
                                _attachButton(l10n),
                                const Spacer(),
                                _sendButton(l10n),
                              ],
                            ),
                          ],
                        )
                      : Row(
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            _plusButton(l10n),
                            _attachButton(l10n),
                            Expanded(child: field),
                            _sendButton(l10n),
                          ],
                        ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _updatePanel(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final warning = context.appColors.warning;
    final label = widget.disabledHint ?? l10n.projectChatNeedsUpdate;
    final canTap = !widget.updating && widget.onUpdate != null;
    return Padding(
      padding: EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          Expanded(
            child: MouseRegion(
              cursor: canTap ? SystemMouseCursors.click : SystemMouseCursors.basic,
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: canTap ? widget.onUpdate : null,
                child: Text(label, style: TextStyle(color: warning)),
              ),
            ),
          ),
          if (widget.updating)
            SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(strokeWidth: 2, color: warning),
            )
          else if (widget.onDismissUpdate != null)
            IconButton(
              visualDensity: VisualDensity.compact,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
              tooltip: l10n.commonCancel,
              onPressed: widget.onDismissUpdate,
              icon: Icon(Icons.close, color: warning),
            ),
        ],
      ),
    );
  }
}
