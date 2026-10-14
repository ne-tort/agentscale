import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Целевой коридор цены сборки: два компактных ввода «от/до» + справа зелёным
/// — коридор возможных комбинаций (price_min..price_max из пайплайна), без
/// пояснений, виден в спокойном состоянии.
class TargetPriceRangeField extends StatefulWidget {
  const TargetPriceRangeField({
    super.key,
    required this.label,
    required this.valueMin,
    required this.valueMax,
    required this.priceMin,
    required this.priceMax,
    required this.readOnly,
    required this.onChanged,
    this.icon,
  });

  final String label;
  final num? valueMin;
  final num? valueMax;
  final num? priceMin;
  final num? priceMax;
  final bool readOnly;
  final void Function(num? min, num? max) onChanged;
  final IconData? icon;

  @override
  State<TargetPriceRangeField> createState() => _TargetPriceRangeFieldState();
}

class _TargetPriceRangeFieldState extends State<TargetPriceRangeField> {
  late final TextEditingController _minCtrl;
  late final TextEditingController _maxCtrl;

  @override
  void initState() {
    super.initState();
    _minCtrl = TextEditingController(text: _fmt(widget.valueMin));
    _maxCtrl = TextEditingController(text: _fmt(widget.valueMax));
  }

  @override
  void didUpdateWidget(TargetPriceRangeField old) {
    super.didUpdateWidget(old);
    // не дёргаем поля, пока пользователь в них печатает
    if (!_minCtrl.selection.isValid) return;
    final newMin = _fmt(widget.valueMin);
    final newMax = _fmt(widget.valueMax);
    if (_minCtrl.text != newMin) _minCtrl.text = newMin;
    if (_maxCtrl.text != newMax) _maxCtrl.text = newMax;
  }

  @override
  void dispose() {
    _minCtrl.dispose();
    _maxCtrl.dispose();
    super.dispose();
  }

  static String _fmt(num? v) {
    if (v == null) return '';
    if (v is double && v == v.roundToDouble()) return v.toInt().toString();
    return v.toString();
  }

  static num? _parse(String raw) {
    final t = raw.trim();
    if (t.isEmpty) return null;
    return int.tryParse(t) ?? double.tryParse(t);
  }

  void _save() {
    widget.onChanged(_parse(_minCtrl.text), _parse(_maxCtrl.text));
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final hasCorridor = widget.priceMin != null || widget.priceMax != null;
    final corridor = hasCorridor
        ? '${_fmt(widget.priceMin)}–${_fmt(widget.priceMax)} ₽'
        : '';
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs,
      ),
      child: Row(
        children: [
          if (widget.icon != null) ...[
            Icon(widget.icon, size: 20, color: colors.muted),
            const SizedBox(width: AppSpacing.md),
          ],
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  widget.label,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: AppSpacing.xs),
                Row(
                  children: [
                    Expanded(child: _numField(_minCtrl, 'от')),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(child: _numField(_maxCtrl, 'до')),
                  ],
                ),
              ],
            ),
          ),
          if (corridor.isNotEmpty) ...[
            const SizedBox(width: AppSpacing.md),
            // коридор возможных комбинаций — зелёным, без пояснений
            Text(
              corridor,
              style: Theme.of(context).textTheme.titleSmall?.copyWith(
                    color: colors.success,
                    fontWeight: FontWeight.w600,
                  ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _numField(TextEditingController ctrl, String hint) {
    return TextField(
      controller: ctrl,
      enabled: !widget.readOnly,
      keyboardType: TextInputType.number,
      decoration: InputDecoration(
        isDense: true,
        hintText: hint,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.sm,
          vertical: AppSpacing.xs,
        ),
        border: const OutlineInputBorder(),
      ),
      onSubmitted: (_) => _save(),
      onEditingComplete: _save,
    );
  }
}
