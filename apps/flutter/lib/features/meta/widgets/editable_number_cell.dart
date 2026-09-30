import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Seamless tap-to-edit numeric table cell (module `editable: true` columns).
///
/// Read mode renders exactly like a plain text cell (no icon / border /
/// fill); tap swaps it for a borderless inline [TextField] (numeric
/// keyboard, autofocus) styled as the cell text itself. Enter submits,
/// Escape cancels, focus loss submits when the text changed. The caller
/// owns persistence (controller.patchField) - the widget formats and parses.
class EditableNumberCell extends StatefulWidget {
  const EditableNumberCell({
    super.key,
    required this.value,
    required this.onSubmit,
    this.align = TextAlign.end,
    this.enabled = true,
  });

  final dynamic value;
  final Future<void> Function(num parsed) onSubmit;
  final TextAlign align;
  final bool enabled;

  @override
  State<EditableNumberCell> createState() => _EditableNumberCellState();
}

class _EditableNumberCellState extends State<EditableNumberCell> {
  TextEditingController? _controller;
  FocusNode? _focus;
  bool _submitting = false;

  String get _initialText {
    final raw = widget.value;
    if (raw is num) {
      return raw == raw.roundToDouble() ? raw.toInt().toString() : raw.toString();
    }
    return raw?.toString() ?? '';
  }

  void _beginEdit() {
    if (!widget.enabled || _submitting) return;
    setState(() {
      _controller = TextEditingController(text: _initialText);
      _focus = FocusNode()
        ..addListener(() {
          if (!(_focus?.hasFocus ?? true)) _submitIfChanged();
        });
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _focus?.requestFocus();
      _controller?.selection =
          TextSelection.collapsed(offset: _controller?.text.length ?? 0);
    });
  }

  void _endEdit() {
    final controller = _controller;
    final focus = _focus;
    setState(() {
      _controller = null;
      _focus = null;
    });
    controller?.dispose();
    focus?.dispose();
  }

  Future<void> _submitIfChanged() async {
    final controller = _controller;
    if (controller == null || _submitting) return;
    final text = controller.text.trim().replaceAll(',', '.');
    if (text == _initialText) {
      _endEdit();
      return;
    }
    final parsed = num.tryParse(text);
    if (parsed == null) {
      _endEdit();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Число не распознано: "$text"'), duration: const Duration(seconds: 2)),
        );
      }
      return;
    }
    setState(() => _submitting = true);
    try {
      await widget.onSubmit(parsed);
    } finally {
      _submitting = false;
      if (mounted) _endEdit();
    }
  }

  @override
  void dispose() {
    _controller?.dispose();
    _focus?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    final style = Theme.of(context).textTheme.bodyMedium;
    if (controller == null) {
      // Read mode: identical to a plain (non-editable) cell - only a
      // hover-friendly ink splash marks the tap target.
      return InkWell(
        onTap: widget.enabled ? _beginEdit : null,
        borderRadius: BorderRadius.circular(4),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
          child: Text(
            _initialText.isEmpty ? '-' : _initialText,
            textAlign: widget.align,
            overflow: TextOverflow.ellipsis,
            maxLines: 1,
            style: style,
          ),
        ),
      );
    }
    // Edit mode: borderless field, cell typography, no background fill.
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
      child: TextField(
        controller: controller,
        focusNode: _focus,
        textAlign: widget.align,
        keyboardType:
            const TextInputType.numberWithOptions(decimal: true, signed: true),
        inputFormatters: [
          FilteringTextInputFormatter.allow(RegExp(r'[0-9.,\-]')),
        ],
        style: style,
        decoration: InputDecoration(
          isDense: true,
          isCollapsed: true,
          border: InputBorder.none,
          enabledBorder: InputBorder.none,
          focusedBorder: InputBorder.none,
          filled: false,
          contentPadding: EdgeInsets.zero,
          suffixIcon: _submitting
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: Padding(
                    padding: EdgeInsets.all(2),
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                )
              : null,
        ),
        onSubmitted: (_) => _submitIfChanged(),
      ),
    );
  }
}
