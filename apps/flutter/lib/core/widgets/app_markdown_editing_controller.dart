import 'package:flutter/material.dart';

/// [TextEditingController] that paints lightweight Markdown syntax spans while editing.
///
/// Covers headings, emphasis, inline/fenced code, links, quotes, and list markers.
/// Not a full CommonMark highlighter — tuned for agent prompt editing.
class AppMarkdownEditingController extends TextEditingController {
  AppMarkdownEditingController({super.text});

  static final _heading = RegExp(r'^#{1,6}\s.*$', multiLine: true);
  static final _bold = RegExp(r'\*\*[^*\n]+?\*\*|__[^_\n]+?__');
  static final _italic = RegExp(r'\*[^*\n]+?\*|_[^_\n]+?_');
  static final _inlineCode = RegExp(r'`[^`\n]+?`');
  static final _fence = RegExp(r'^```[\s\S]*?^```', multiLine: true);
  static final _link = RegExp(r'\[[^\]]+\]\([^)]+\)');
  static final _quote = RegExp(r'^>\s?.*$', multiLine: true);
  static final _list = RegExp(r'^(\s*[-*+]|\s*\d+\.)\s', multiLine: true);

  @override
  TextSpan buildTextSpan({
    required BuildContext context,
    TextStyle? style,
    required bool withComposing,
  }) {
    final base = style ?? const TextStyle();
    final scheme = Theme.of(context).colorScheme;
    final text = value.text;
    if (text.isEmpty) {
      return TextSpan(style: base, text: text);
    }

    final marks = <_Mark>[];
    void addAll(RegExp re, TextStyle style) {
      for (final m in re.allMatches(text)) {
        marks.add(_Mark(m.start, m.end, style));
      }
    }

    addAll(
      _fence,
      base.copyWith(color: scheme.tertiary, fontFamily: 'monospace'),
    );
    addAll(
      _heading,
      base.copyWith(
        color: scheme.primary,
        fontWeight: FontWeight.w700,
      ),
    );
    addAll(
      _bold,
      base.copyWith(fontWeight: FontWeight.w700, color: scheme.onSurface),
    );
    addAll(
      _italic,
      base.copyWith(fontStyle: FontStyle.italic, color: scheme.onSurfaceVariant),
    );
    addAll(
      _inlineCode,
      base.copyWith(
        color: scheme.secondary,
        fontFamily: 'monospace',
        backgroundColor: scheme.surfaceContainerHighest.withValues(alpha: 0.55),
      ),
    );
    addAll(
      _link,
      base.copyWith(
        color: scheme.primary,
        decoration: TextDecoration.underline,
      ),
    );
    addAll(
      _quote,
      base.copyWith(color: scheme.onSurfaceVariant, fontStyle: FontStyle.italic),
    );
    addAll(
      _list,
      base.copyWith(color: scheme.primary, fontWeight: FontWeight.w600),
    );

    marks.sort((a, b) => a.start.compareTo(b.start));
    final merged = <_Mark>[];
    for (final m in marks) {
      if (merged.isEmpty || m.start >= merged.last.end) {
        merged.add(m);
      }
    }

    final children = <InlineSpan>[];
    var cursor = 0;
    for (final m in merged) {
      if (m.start > cursor) {
        children.add(TextSpan(text: text.substring(cursor, m.start), style: base));
      }
      children.add(TextSpan(text: text.substring(m.start, m.end), style: m.style));
      cursor = m.end;
    }
    if (cursor < text.length) {
      children.add(TextSpan(text: text.substring(cursor), style: base));
    }

    return TextSpan(style: base, children: children);
  }
}

class _Mark {
  const _Mark(this.start, this.end, this.style);
  final int start;
  final int end;
  final TextStyle style;
}
