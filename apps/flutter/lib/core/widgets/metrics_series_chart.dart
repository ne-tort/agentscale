import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Compact CPU/RAM history chart for the authorized project metrics response.
///
/// The API currently returns a list of sample maps, but this widget also accepts
/// the common `data`/`items`/`points` wrappers and a `latest` sample fallback so
/// an intermediate response shape does not silently remove the metrics UI.
class MetricsSeriesChart extends StatelessWidget {
  const MetricsSeriesChart({
    super.key,
    required this.metrics,
    this.hasError = false,
  });

  final Map<String, dynamic>? metrics;
  final bool hasError;

  @override
  Widget build(BuildContext context) {
    final response = metrics;
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
    final textTheme = Theme.of(context).textTheme;
    if (response == null) {
      if (!hasError) return const SizedBox.shrink();
      return _StateCard(
        icon: Icons.error_outline,
        color: colors.danger,
        message: l10n.metricsSeriesError,
      );
    }
    final rawError = response['error'] ?? response['metrics_error'];
    if (hasError || _isError(rawError)) {
      return _StateCard(
        icon: Icons.error_outline,
        color: colors.danger,
        message: l10n.metricsSeriesError,
      );
    }

    final samples = _samples(response);
    final cpu = _values(samples, _cpuKeys);
    final memory = _values(samples, _memoryKeys);
    final unavailable = response['metrics_available'] == false;
    if (unavailable) {
      return _StateCard(
        icon: Icons.info_outline,
        color: colors.warning,
        message: l10n.metricsSeriesUnavailable,
      );
    }
    if (!_hasValues(cpu) && !_hasValues(memory)) {
      return _StateCard(
        icon: Icons.show_chart_outlined,
        color: colors.muted,
        message: l10n.metricsSeriesEmpty,
      );
    }

    final cpuColor = colors.primary;
    final memoryColor = colors.info;
    final cpuLast = _last(cpu);
    final memoryLast = _last(memory);
    final window = response['window'];
    final windowLabel = window is String && window.trim().isNotEmpty ? window : null;

    return Card(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                _Legend(color: cpuColor, label: l10n.metricsSeriesCpu),
                const SizedBox(width: AppSpacing.md),
                _Legend(color: memoryColor, label: l10n.metricsSeriesMemory),
                if (windowLabel != null) ...[
                  const Spacer(),
                  Text(
                    windowLabel,
                    style: textTheme.labelSmall?.copyWith(color: colors.muted),
                  ),
                ],
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            SizedBox(
              height: 92,
              width: double.infinity,
              child: CustomPaint(
                painter: _MetricsSeriesPainter(
                  cpu: cpu,
                  memory: memory,
                  cpuColor: cpuColor,
                  memoryColor: memoryColor,
                  gridColor: colors.border,
                ),
                child: Semantics(
                  label: [
                    if (cpuLast != null)
                      l10n.metricsSeriesCpuValue('${cpuLast.round()} millicores'),
                    if (memoryLast != null)
                      l10n.metricsSeriesMemoryValue('${memoryLast.round()} bytes'),
                  ].join(', '),
                  child: const SizedBox.expand(),
                ),
              ),
            ),
            const SizedBox(height: AppSpacing.xs),
            Row(
              children: [
                Expanded(
                  child: Text(
                    l10n.metricsSeriesCpuValue(
                      cpuLast == null ? l10n.commonEmDash : '${cpuLast.round()}m',
                    ),
                    style: textTheme.labelSmall?.copyWith(color: cpuColor),
                  ),
                ),
                Expanded(
                  child: Text(
                    l10n.metricsSeriesMemoryValue(
                      memoryLast == null ? l10n.commonEmDash : _formatBytes(memoryLast),
                    ),
                    textAlign: TextAlign.end,
                    style: textTheme.labelSmall?.copyWith(color: memoryColor),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  static const _cpuKeys = <String>[
    'cpu_millicores',
    'cpuMillicores',
    'cpu',
  ];
  static const _memoryKeys = <String>[
    'memory_bytes',
    'memoryBytes',
    'memory',
  ];

  static bool _isError(Object? value) {
    if (value is bool) return value;
    return value is String && value.trim().isNotEmpty;
  }

  static List<Map<String, dynamic>> _samples(Map<String, dynamic> response) {
    final raw = response['series'];
    final samples = _samplesFromRaw(raw);
    if (samples.isNotEmpty) return samples;

    final latest = response['latest'];
    if (latest is Map) return [Map<String, dynamic>.from(latest)];
    return const [];
  }

  static List<Map<String, dynamic>> _samplesFromRaw(Object? raw) {
    if (raw is List) {
      return [
        for (final value in raw)
          if (value is Map) Map<String, dynamic>.from(value),
      ];
    }
    if (raw is! Map) return const [];

    for (final key in const ['data', 'items', 'points', 'samples']) {
      final nested = raw[key];
      if (nested is List) return _samplesFromRaw(nested);
    }

    final cpuValues = _listValue(raw, _cpuKeys);
    final memoryValues = _listValue(raw, _memoryKeys);
    if (cpuValues == null && memoryValues == null) {
      return [Map<String, dynamic>.from(raw)];
    }

    final length = [cpuValues?.length ?? 0, memoryValues?.length ?? 0].reduce(
      (a, b) => a > b ? a : b,
    );
    return [
      for (var i = 0; i < length; i++)
        <String, dynamic>{
          if (cpuValues != null && i < cpuValues.length) 'cpu_millicores': cpuValues[i],
          if (memoryValues != null && i < memoryValues.length) 'memory_bytes': memoryValues[i],
        },
    ];
  }

  static List<Object?>? _listValue(Map<dynamic, dynamic> map, List<String> keys) {
    for (final key in keys) {
      final value = map[key];
      if (value is List) return value;
    }
    return null;
  }

  static List<double?> _values(List<Map<String, dynamic>> samples, List<String> keys) => [
        for (final sample in samples) _number(sample, keys),
      ];

  static double? _number(Map<String, dynamic> sample, List<String> keys) {
    for (final key in keys) {
      final value = sample[key];
      if (value is num) return value.toDouble();
      if (value is String) {
        final parsed = double.tryParse(value.trim());
        if (parsed != null) return parsed;
      }
    }
    final nested = sample['metrics'];
    if (nested is Map) return _number(Map<String, dynamic>.from(nested), keys);
    return null;
  }

  static bool _hasValues(List<double?> values) => values.any((v) => v != null);

  static double? _last(List<double?> values) {
    for (final value in values.reversed) {
      if (value != null) return value;
    }
    return null;
  }

  static String _formatBytes(double value) {
    if (value >= 1024 * 1024 * 1024) return '${(value / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
    if (value >= 1024 * 1024) return '${(value / (1024 * 1024)).toStringAsFixed(1)} MB';
    if (value >= 1024) return '${(value / 1024).toStringAsFixed(1)} KB';
    return '${value.round()} B';
  }
}

class _StateCard extends StatelessWidget {
  const _StateCard({required this.icon, required this.color, required this.message});

  final IconData icon;
  final Color color;
  final String message;

  @override
  Widget build(BuildContext context) => Card(
        margin: const EdgeInsets.only(bottom: AppSpacing.md),
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: color, size: 20),
              const SizedBox(width: AppSpacing.sm),
              Expanded(child: Text(message)),
            ],
          ),
        ),
      );
}

class _Legend extends StatelessWidget {
  const _Legend({required this.color, required this.label});

  final Color color;
  final String label;

  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
          const SizedBox(width: AppSpacing.xs),
          Text(label, style: Theme.of(context).textTheme.labelMedium),
        ],
      );
}

class _MetricsSeriesPainter extends CustomPainter {
  const _MetricsSeriesPainter({
    required this.cpu,
    required this.memory,
    required this.cpuColor,
    required this.memoryColor,
    required this.gridColor,
  });

  final List<double?> cpu;
  final List<double?> memory;
  final Color cpuColor;
  final Color memoryColor;
  final Color gridColor;

  @override
  void paint(Canvas canvas, Size size) {
    final grid = Paint()
      ..color = gridColor.withValues(alpha: 0.45)
      ..strokeWidth = 1;
    for (var i = 1; i <= 3; i++) {
      final y = size.height * i / 4;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }
    _drawLine(canvas, size, cpu, cpuColor);
    _drawLine(canvas, size, memory, memoryColor);
  }

  void _drawLine(Canvas canvas, Size size, List<double?> values, Color color) {
    final valid = values.whereType<double>().toList();
    if (valid.isEmpty) return;
    final min = valid.reduce((a, b) => a < b ? a : b);
    final max = valid.reduce((a, b) => a > b ? a : b);
    final range = max - min;
    final path = Path();
    var started = false;
    for (var i = 0; i < values.length; i++) {
      final value = values[i];
      if (value == null) {
        started = false;
        continue;
      }
      final x = values.length <= 1 ? size.width / 2 : size.width * i / (values.length - 1);
      final y = size.height - 6 - (range == 0 ? size.height / 2 : (value - min) / range * (size.height - 12));
      final point = Offset(x, y);
      if (started) {
        path.lineTo(point.dx, point.dy);
      } else {
        path.moveTo(point.dx, point.dy);
        started = true;
      }
      canvas.drawCircle(point, 2.5, Paint()..color = color);
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = color
        ..strokeWidth = 2
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round,
    );
  }

  @override
  bool shouldRepaint(covariant _MetricsSeriesPainter oldDelegate) =>
      oldDelegate.cpu != cpu ||
      oldDelegate.memory != memory ||
      oldDelegate.cpuColor != cpuColor ||
      oldDelegate.memoryColor != memoryColor ||
      oldDelegate.gridColor != gridColor;
}
