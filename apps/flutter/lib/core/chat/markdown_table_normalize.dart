/// Normalize LLM / non-GFM pipe tables so [MarkdownBody] TableSyntax can parse them.
///
/// Handles:
/// - rows glued into one line (`|| a | b || |---| || c | d ||`)
/// - double-pipe row wrappers (`|| … ||` → `| … |`)
/// - missing separator row after a header
///
/// Fenced code blocks (``` / ~~~) are never touched — normalization must not
/// rewrite code samples, no matter how pipe-rich they look.
String normalizeChatMarkdownTables(String source) {
  if (!source.contains('|')) return source;

  final lines = <String>[];
  final fenced = <bool>[];
  final fenceRe = RegExp(r'^\s{0,3}(`{3,}|~{3,})');
  var fenceMarker = '';
  var fenceLen = 0;
  for (final line in source.split('\n')) {
    final m = fenceRe.firstMatch(line);
    if (m != null) {
      final marker = m.group(1)![0];
      final len = m.group(1)!.length;
      final rest = line.substring(m.end).trim();
      if (fenceMarker.isEmpty) {
        fenceMarker = marker;
        fenceLen = len;
      } else if (marker == fenceMarker && len >= fenceLen && rest.isEmpty) {
        // Closing fence: same marker, at least as long, nothing after it.
        fenceMarker = '';
      }
      lines.add(line);
      fenced.add(true);
      continue;
    }
    if (fenceMarker.isEmpty) {
      final expanded = _expandPossiblyGluedTableLine(line);
      lines.addAll(expanded);
      for (var k = 0; k < expanded.length; k++) {
        fenced.add(false);
      }
    } else {
      lines.add(line);
      fenced.add(true);
    }
  }
  return _ensureTableSeparators(lines, fenced).join('\n');
}

final _separatorRowRe = RegExp(
  r'^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$',
);

bool _looksLikeSeparator(String line) => _separatorRowRe.hasMatch(line.trim());

bool _looksLikeTableRow(String line) {
  final t = line.trim();
  if (!t.contains('|')) return false;
  if (_looksLikeSeparator(t)) return true;
  return RegExp(r'\|').allMatches(t).length >= 2;
}

List<String> _expandPossiblyGluedTableLine(String line) {
  final trimmed = line.trim();
  if (!trimmed.contains('|')) return [line];

  final hasDoublePipe = trimmed.contains('||');
  final hasSep = RegExp(r'\|[\t ]*:?-+:?[\t ]*\|').hasMatch(trimmed);
  final gluedBoundary = RegExp(r'\|\s+\|').hasMatch(trimmed);

  // Ordinary single markdown table row (or prose) — leave unchanged.
  if (!hasDoublePipe && !gluedBoundary) {
    return [line];
  }

  if (hasDoublePipe) {
    final rows = _splitDoublePipeRows(trimmed);
    if (rows.isNotEmpty) return rows;
  }

  // Glued `| a | b | |---| | c | d |` → break before next row / separator.
  var work = trimmed;
  work = work.replaceAllMapped(
    RegExp(r'\|\s+(\|(?:[\t ]*:?-+:?[\t ]*\|)+)'),
    (m) => '|\n${m.group(1)}',
  );
  work = work.replaceAllMapped(
    RegExp(r'\|\s+\|(?=\s*[^|\s-])'),
    (_) => '|\n|',
  );

  // If separator was mid-line without the glued-boundary pattern above.
  if (hasSep && !work.contains('\n')) {
    work = work.replaceAllMapped(
      RegExp(r'\|(\s*\|[\t ]*:?-+:?[\t ]*(?:\|[\t ]*:?-+:?[\t ]*)*\|?)'),
      (m) {
        final frag = m.group(1)!;
        if (_looksLikeSeparator(frag) || _looksLikeSeparator(frag.startsWith('|') ? frag : '|$frag')) {
          return '|\n${frag.startsWith('|') ? frag : '|$frag'}';
        }
        return m.group(0)!;
      },
    );
  }

  final parts = work
      .split('\n')
      .map(_normalizeTableRow)
      .where((s) => s.trim().isNotEmpty)
      .toList();
  return parts.isEmpty ? [line] : parts;
}

List<String> _splitDoublePipeRows(String line) {
  final rows = <String>[];
  final re = RegExp(r'\|\|(.+?)\|\|');
  var last = 0;
  var matched = false;
  for (final m in re.allMatches(line)) {
    matched = true;
    final between = line.substring(last, m.start).trim();
    if (between.isNotEmpty) {
      rows.add(_normalizeTableRow(between));
    }
    rows.add(_normalizeTableRow('|${m.group(1)!.trim()}|'));
    last = m.end;
  }
  if (!matched) return const [];
  final rest = line.substring(last).trim();
  if (rest.isNotEmpty) rows.add(_normalizeTableRow(rest));
  return rows.where((s) => s.trim().isNotEmpty).toList();
}

String _normalizeTableRow(String row) {
  var r = row.trim();
  while (r.startsWith('||')) {
    r = r.substring(1).trimLeft();
  }
  while (r.endsWith('||')) {
    r = r.substring(0, r.length - 1).trimRight();
  }
  r = r.replaceAll('||', '|');
  if (!_looksLikeTableRow(r) && !_looksLikeSeparator(r)) {
    return r;
  }
  if (!r.startsWith('|')) r = '| $r';
  if (!r.endsWith('|')) r = '$r |';
  if (_looksLikeSeparator(r)) {
    return r.replaceAll(RegExp(r'\s+'), '');
  }
  return r;
}

List<String> _ensureTableSeparators(List<String> lines, List<bool> fenced) {
  if (lines.length < 2) return lines;
  final out = <String>[];
  for (var i = 0; i < lines.length; i++) {
    final cur = lines[i];
    final curFenced = i < fenced.length ? fenced[i] : false;
    out.add(cur);
    if (curFenced) continue;
    if (!_looksLikeTableRow(cur) || _looksLikeSeparator(cur)) continue;
    if (i + 1 >= lines.length) continue;
    final next = lines[i + 1];
    final nextFenced = i + 1 < fenced.length ? fenced[i + 1] : false;
    if (nextFenced) continue;
    if (!_looksLikeTableRow(next) || _looksLikeSeparator(next)) continue;
    // Only between first header row and following body when separator is missing.
    final prev = out.length >= 2 ? out[out.length - 2] : null;
    final startOfTable = prev == null || !_looksLikeTableRow(prev);
    if (!startOfTable) continue;
    final cols = _columnCount(cur);
    if (cols < 2) continue;
    out.add(_separatorForColumns(cols));
  }
  return out;
}

int _columnCount(String row) {
  var inner = row.trim();
  if (inner.startsWith('|')) inner = inner.substring(1);
  if (inner.endsWith('|')) inner = inner.substring(0, inner.length - 1);
  return inner.split('|').length;
}

String _separatorForColumns(int cols) {
  final cells = List.filled(cols, '---');
  return '|${cells.join('|')}|';
}
