import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_multiline_text_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';

/// Full-page multiline editor: saves on back and when the field loses focus.
///
/// No explicit «Done» action — [onChanged] is the source of truth while editing;
/// [Navigator.pop] returns the latest text (or null when [readOnly]).
class AppMultilineEditorPage extends StatefulWidget {
  const AppMultilineEditorPage({
    super.key,
    required this.title,
    required this.initial,
    this.readOnly = false,
    this.markdown = false,
    this.onChanged,
    this.header,
  });

  final String title;
  final String initial;
  final bool readOnly;
  final bool markdown;
  /// Fired on every keystroke and again when committing (blur / back).
  final ValueChanged<String>? onChanged;
  /// Optional widgets above the editor (e.g. [AppValuePreference] for rename).
  final Widget? header;

  @override
  State<AppMultilineEditorPage> createState() => _AppMultilineEditorPageState();
}

class _AppMultilineEditorPageState extends State<AppMultilineEditorPage> {
  late String _text;
  final _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _text = widget.initial;
  }

  @override
  void dispose() {
    _focus.dispose();
    super.dispose();
  }

  void _commit() {
    if (widget.readOnly) return;
    widget.onChanged?.call(_text);
  }

  void _pop() {
    _commit();
    if (!mounted) return;
    Navigator.of(context).pop(widget.readOnly ? null : _text);
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop) return;
        _pop();
      },
      child: AppScaffold(
        title: Text(widget.title),
        body: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (widget.header != null) ...[
                widget.header!,
                const SizedBox(height: AppSpacing.md),
              ],
              Expanded(
                child: AppMultilineTextField(
                  value: _text,
                  readOnly: widget.readOnly,
                  markdown: widget.markdown,
                  expands: true,
                  focusNode: _focus,
                  onChanged: (v) {
                    _text = v;
                    widget.onChanged?.call(v);
                  },
                  onEditingComplete: _commit,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
