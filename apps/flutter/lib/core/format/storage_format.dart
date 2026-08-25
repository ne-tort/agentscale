/// Format storage bytes as human-readable value with unit in counter (e.g. "34 Gb").
String formatStorageGb(num bytes) {
  if (bytes <= 0) return '0 Gb';
  final gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 10) return '${gb.round()} Gb';
  if (gb >= 1) return '${gb.toStringAsFixed(1)} Gb';
  final mb = bytes / (1024 * 1024);
  if (mb >= 1) return '${mb.toStringAsFixed(1)} Mb';
  final kb = bytes / 1024;
  if (kb >= 1) return '${kb.toStringAsFixed(1)} Kb';
  return '$bytes b';
}
