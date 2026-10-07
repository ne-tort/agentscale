import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/chat_clipboard.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/l10n/app_localizations.dart';

export 'package:prodavan/core/chat/chat_clipboard.dart' show DroppedChatFile;

/// Wide limits aligned with API agent constraints (not unbounded).
const int kChatMaxMessageChars = 500000;
const int kChatMaxAttachmentsPerMessage = 32;
const int kChatMaxAttachmentBytesClient = 500 * 1024 * 1024; // platform ceiling; server enforces company policy
const int kComposerDraftMinChars = 5;

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
    this.modelLabel,
    this.onPickModel,
    this.projectId,
    this.sessionId,
    this.api,
    this.onSessionMaterialized,
    this.onDraftPresenceChanged,
    this.draftRestore,
    this.wakeMode = false,
    this.waking = false,
    this.onWake,
    this.updateMode = false,
    this.updating = false,
    this.onUpdate,
    this.onDismissUpdate,
    this.clipboardReader,
  });

  final ChatComposerSend onSend;
  final bool enabled;
  final bool streaming;
  final String? disabledHint;
  final VoidCallback? onCancel;
  final VoidCallback? onOpenSettings;

  /// Label for the quick model-switch pill (see [onPickModel]).
  final String? modelLabel;

  /// Opens the model picker sheet; the pill is hidden when null.
  final VoidCallback? onPickModel;
  final String? projectId;
  /// Null / empty → pending «Новый диалог» (no session yet).
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String sessionId)? onSessionMaterialized;
  final VoidCallback? onDraftPresenceChanged;
  /// Interrupted-turn draft restore (SSE drop): refill the field when set
  /// by the chat controller so the user message is not lost.
  final ValueNotifier<String?>? draftRestore;
  /// When true, field is not sendable but tappable — [onWake] resumes/reloads.
  final bool wakeMode;
  /// In-progress resume/reload — spinner on wake panel, ignore further taps.
  final bool waking;
  final VoidCallback? onWake;
  /// Workspace outdated — block input; tap updates, X dismisses the mark.
  final bool updateMode;
  /// Clipboard source for paste-to-attach (Ctrl+V of files/images); defaults
  /// to the pasteboard-backed reader. Injectable for tests.
  final ChatClipboardReader? clipboardReader;
  final bool updating;
  final VoidCallback? onUpdate;
  final VoidCallback? onDismissUpdate;

  @override
  State<ChatComposer> createState() => ChatComposerState();
}

class ChatComposerState extends State<ChatComposer> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  // Stable key for the Focus widget so that when the layout switches between
  // the single-line Row and the multi-line Column (buttons moving under the
  // field), Flutter reparents the Focus subtree instead of rebuilding it.
  // Without it, the Focus widget is recreated under a new parent and the
  // TextField loses primary focus on the first newline.
  final _focusKey = GlobalKey();
  final List<_PendingAttachment> _attachments = [];
  bool _uploading = false;
  bool _draftHydrated = false;
  bool _multiline = false;
  /// After Send, ignore the empty-text persist that would clear the server draft
  /// before the user message is written (sidebar GC race → 404).
  bool _suppressDraftPersist = false;
  Timer? _draftTimer;
  String? _lastPersistedDraft;

  @override
  void initState() {
    super.initState();
    _controller.addListener(_onTextChanged);
    widget.draftRestore?.addListener(_onDraftRestore);
    unawaited(_hydrateDraft());
  }

  @override
  void didUpdateWidget(covariant ChatComposer oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.draftRestore != widget.draftRestore) {
      oldWidget.draftRestore?.removeListener(_onDraftRestore);
      widget.draftRestore?.addListener(_onDraftRestore);
    }
    if (oldWidget.sessionId != widget.sessionId || oldWidget.projectId != widget.projectId) {
      _draftHydrated = false;
      _lastPersistedDraft = null;
      unawaited(_hydrateDraft());
    }
  }

  @override
  void dispose() {
    _draftTimer?.cancel();
    _controller.removeListener(_onTextChanged);
    widget.draftRestore?.removeListener(_onDraftRestore);
    _focusNode.dispose();
    _controller.dispose();
    super.dispose();
  }

  /// Interrupted-turn draft restore (SSE drop): refill the input field with
  /// the user's text unless they already started typing something new.
  void _onDraftRestore() {
    final draft = widget.draftRestore?.value;
    if (draft == null || draft.trim().isEmpty) return;
    if (_controller.text.trim().isNotEmpty) return;
    _controller.value = TextEditingValue(text: draft, selection: TextSelection.collapsed(offset: draft.length));
    _onTextChanged();
  }

  bool get _canSend =>
      widget.enabled &&
      !widget.streaming &&
      !_uploading &&
      (_controller.text.trim().isNotEmpty || _attachments.isNotEmpty);

  bool get _hasSession {
    final sid = widget.sessionId;
    return sid != null && sid.isNotEmpty;
  }

  Future<void> _hydrateDraft() async {
    final projectId = widget.projectId;
    final api = widget.api;
    if (projectId == null || api == null) {
      _draftHydrated = true;
      return;
    }
    try {
      final body = _hasSession
          ? await api.getSessionComposerDraft(projectId: projectId, sessionId: widget.sessionId!)
          : await api.getProjectComposerDraft(projectId: projectId);
      final text = (body['text'] as String?) ?? '';
      if (!mounted) return;
      if (text.isNotEmpty && _controller.text.isEmpty) {
        _controller.value = TextEditingValue(
          text: text,
          selection: TextSelection.collapsed(offset: text.length),
        );
        _lastPersistedDraft = text;
      }
      final materialized = body['session_id'] as String?;
      if (!_hasSession && materialized != null && materialized.isNotEmpty) {
        widget.onSessionMaterialized?.call(materialized);
      }
    } catch (_) {
      // Draft is best-effort.
    } finally {
      _draftHydrated = true;
    }
  }

  void _scheduleDraftPersist() {
    if (!_draftHydrated || _suppressDraftPersist) return;
    _draftTimer?.cancel();
    _draftTimer = Timer(const Duration(milliseconds: 450), () {
      unawaited(_persistDraft());
    });
  }

  Future<void> _persistDraft() async {
    if (_suppressDraftPersist) return;
    final projectId = widget.projectId;
    final api = widget.api;
    if (projectId == null || api == null) return;
    final text = _controller.text;
    final trimmed = text.trim();
    final shouldStore = trimmed.length >= kComposerDraftMinChars;
    final payload = shouldStore ? text : '';
    final prev = _lastPersistedDraft ?? '';
    if (payload == prev) return;
    if (!shouldStore && prev.isEmpty) return;
    try {
      if (_hasSession) {
        final body = await api.putSessionComposerDraft(
          projectId: projectId,
          sessionId: widget.sessionId!,
          text: payload,
        );
        _lastPersistedDraft = (body['text'] as String?) ?? '';
        widget.onDraftPresenceChanged?.call();
      } else {
        final body = await api.putProjectComposerDraft(projectId: projectId, text: payload);
        _lastPersistedDraft = (body['text'] as String?) ?? '';
        final sid = body['session_id'] as String?;
        if (body['materialized'] == true && sid != null && sid.isNotEmpty) {
          widget.onSessionMaterialized?.call(sid);
        }
        widget.onDraftPresenceChanged?.call();
      }
    } catch (_) {
      // Ignore transient draft errors.
    }
  }

  void _onTextChanged() {
    if (!mounted) return;
    setState(() {});
    _scheduleDraftPersist();
  }

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
    final lineHeight = (style.fontSize ?? 16) * (style.height ?? 1.2);
    return painter.height > lineHeight * 1.4;
  }

  void _submit() {
    if (!_canSend) return;
    final l10n = AppLocalizations.of(context);
    final text = _controller.text;
    if (text.length > kChatMaxMessageChars) {
      AppSnackBar.warning(context, l10n.chatMessageTooLong(kChatMaxMessageChars));
      return;
    }
    if (_attachments.length > kChatMaxAttachmentsPerMessage) {
      AppSnackBar.warning(context, l10n.chatTooManyAttachments(kChatMaxAttachmentsPerMessage));
      return;
    }
    _draftTimer?.cancel();
    _suppressDraftPersist = true;
    _lastPersistedDraft = '';
    widget.onSend(
      text,
      _attachments.map((a) => a.id).toList(),
    );
    _controller.clear();
    setState(_attachments.clear);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _suppressDraftPersist = false;
    });
  }

  KeyEventResult _handleKeyEvent(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.ignored;
    // Ctrl+V / Cmd+V: plain text keeps the native TextField paste (the
    // event is NOT consumed); files/images from the clipboard attach via
    // the async reader — the native paste is a no-op for binary clipboard.
    final isPaste = event.logicalKey == LogicalKeyboardKey.keyV &&
        (HardwareKeyboard.instance.isControlPressed ||
            HardwareKeyboard.instance.isMetaPressed);
    if (isPaste) {
      unawaited(_handlePaste());
      return KeyEventResult.ignored;
    }
    if (event.logicalKey != LogicalKeyboardKey.enter) return KeyEventResult.ignored;
    if (HardwareKeyboard.instance.isShiftPressed) return KeyEventResult.ignored;
    if (!widget.enabled || widget.streaming) return KeyEventResult.handled;
    _submit();
    return KeyEventResult.handled;
  }

  Future<void> _handlePaste() async {
    final projectId = widget.projectId;
    final api = widget.api;
    if (!widget.enabled || projectId == null || api == null || _uploading) return;
    final reader = widget.clipboardReader ?? const PasteboardClipboardReader();
    final files = await reader.readFilesOrImage();
    if (files.isEmpty || !mounted) return;
    await attachDroppedFiles(files);
  }

  Future<void> _pickFile() async {
    final projectId = widget.projectId;
    final api = widget.api;
    if (!widget.enabled || projectId == null || api == null || _uploading) return;

    // No client-side type filter: the backend whitelists extensions and
    // rejects forbidden content (executables/scripts) with a domain error,
    // which AppErrors maps to a themed snackbar. Picking any file here keeps
    // the UX consistent — unsupported types show a domain error, not a silent
    // no-op or a generic white snackbar.
    FilePickerResult? result;
    try {
      result = await FilePicker.platform.pickFiles(withData: true);
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      return;
    }
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    final bytes = file.bytes;
    if (bytes == null) return;
    if (!mounted) return;
    await _uploadAttachment(
      api: api,
      projectId: projectId,
      filename: file.name,
      bytes: bytes,
    );
  }

  /// Files dropped onto the chat area (drag & drop) enter the exact same
  /// pipeline as the attach button: per-file limits, upload, pending chips
  /// and error snackbars.
  Future<void> attachDroppedFiles(List<DroppedChatFile> files) async {
    final projectId = widget.projectId;
    final api = widget.api;
    if (!widget.enabled || projectId == null || api == null || _uploading) return;
    for (final dropped in files) {
      if (!mounted) return;
      if (!_checkAttachmentLimits(dropped.bytes)) return;
      await _uploadAttachment(
        api: api,
        projectId: projectId,
        filename: dropped.name,
        bytes: dropped.bytes,
      );
    }
  }

  bool _checkAttachmentLimits(Uint8List bytes) {
    if (_attachments.length >= kChatMaxAttachmentsPerMessage) {
      AppSnackBar.warning(
        context,
        AppLocalizations.of(context).chatTooManyAttachments(kChatMaxAttachmentsPerMessage),
      );
      return false;
    }
    if (bytes.length > kChatMaxAttachmentBytesClient) {
      AppSnackBar.warning(context, AppLocalizations.of(context).chatFileTooLarge);
      return false;
    }
    return true;
  }

  Future<void> _uploadAttachment({
    required ProdavanApi api,
    required String projectId,
    required String filename,
    required Uint8List bytes,
  }) async {
    setState(() => _uploading = true);
    try {
      final body = await api.uploadProjectAttachment(
        projectId: projectId,
        filename: filename,
        bytes: bytes,
      );
      final id = body['id'] as String? ?? body['storage_ref'] as String? ?? '';
      if (id.isEmpty) {
        if (mounted) {
          AppSnackBar.warning(
            context,
            AppLocalizations.of(context).chatAttachmentUploadFailed,
          );
        }
        return;
      }
      setState(() {
        _attachments.add(_PendingAttachment(id: id, filename: filename));
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
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

  /// Quick model-switch pill next to Send. Visible whenever [widget.onPickModel]
  /// is set; dimmed and inert while disabled or streaming (aligns with
  /// [_canSend] semantics without hiding the current model).
  Widget _modelPill() {
    if (widget.onPickModel == null) return const SizedBox.shrink();
    final scheme = Theme.of(context).colorScheme;
    final muted = scheme.onSurfaceVariant;
    final active = widget.enabled && !widget.streaming;
    final label = (widget.modelLabel ?? '').trim();
    return Padding(
      padding: const EdgeInsets.only(right: AppSpacing.xs),
      child: Opacity(
        opacity: active ? 1 : 0.5,
        child: InkWell(
          borderRadius: BorderRadius.circular(8),
          onTap: active ? widget.onPickModel : null,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            constraints: const BoxConstraints(minHeight: 36),
            decoration: BoxDecoration(
              color: scheme.surfaceContainerHigh,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.smart_toy_outlined, size: 14, color: muted),
                const SizedBox(width: 4),
                ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 140),
                  child: Text(
                    label.isEmpty ? '—' : label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.labelMedium
                        ?.copyWith(color: muted),
                  ),
                ),
                const SizedBox(width: 2),
                Icon(Icons.expand_more, size: 14, color: muted),
              ],
            ),
          ),
        ),
      ),
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
      tooltip: l10n.commonSendAction,
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
      key: _focusKey,
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
      // Layout switches Row↔Column; remember focus so we can restore it in a
      // post-frame callback after the new tree mounts (otherwise the field
      // loses primary focus and the user must click back into it).
      final hadFocus = _focusNode.hasFocus;
      _multiline = multiline;
      if (hadFocus) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!mounted) return;
          if (!_focusNode.hasFocus) {
            _focusNode.requestFocus();
          }
        });
      }
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
                                _modelPill(),
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
                            _modelPill(),
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
