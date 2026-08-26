import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Date preference (DD.MM.YY / DD.MM.YYYY). Empty = [emptyLabel] (default unlimited).
///
/// Empty dates are allowed (lifetime / not set). Non-empty must be a real calendar
/// date not before **today UTC**.
class AppSubscriptionPreference extends StatefulWidget {
  const AppSubscriptionPreference({
    super.key,
    required this.endsAt,
    required this.onEndsAtSave,
    this.enabled = true,
    this.title,
    this.emptyLabel,
    this.icon = Icons.event_rounded,
    this.accentColor,
  });

  final String endsAt;
  final Future<void> Function(String endsAt) onEndsAtSave;
  final bool enabled;
  final String? title;
  final String? emptyLabel;
  final IconData icon;
  final Color? accentColor;

  static final _datePattern = RegExp(r'^\d{2}\.\d{2}\.\d{2,4}$');

  /// Empty OK; otherwise valid calendar day ≥ today (UTC date).
  static bool isValidDate(String raw) {
    final trimmed = raw.trim();
    if (trimmed.isEmpty) return true;
    if (!_datePattern.hasMatch(trimmed)) return false;
    final parsed = parseSubscriptionDateUtc(trimmed);
    if (parsed == null) return false;
    final now = DateTime.now().toUtc();
    final todayUtc = DateTime.utc(now.year, now.month, now.day);
    return !parsed.isBefore(todayUtc);
  }

  @override
  State<AppSubscriptionPreference> createState() =>
      _AppSubscriptionPreferenceState();
}

class _AppSubscriptionPreferenceState extends State<AppSubscriptionPreference> {
  late TextEditingController _controller;
  late FocusNode _focusNode;
  bool _expanded = false;
  bool _saving = false;
  bool _ignoreNextBlur = false;
  bool _invalid = false;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.endsAt);
    _focusNode = FocusNode()..addListener(_onFocusChange);
  }

  @override
  void didUpdateWidget(covariant AppSubscriptionPreference oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_expanded) {
      _controller.text = widget.endsAt;
    }
  }

  @override
  void dispose() {
    _focusNode.removeListener(_onFocusChange);
    _focusNode.dispose();
    _controller.dispose();
    super.dispose();
  }

  void _onFocusChange() {
    if (_focusNode.hasFocus || !_expanded || _saving) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _focusNode.hasFocus || !_expanded || _saving) return;
      if (_ignoreNextBlur) {
        _ignoreNextBlur = false;
        return;
      }
      _cancel();
    });
  }

  void _guardBlur() => _ignoreNextBlur = true;

  void _cancel() {
    setState(() {
      _expanded = false;
      _invalid = false;
      _controller.text = widget.endsAt;
    });
    _focusNode.unfocus();
  }

  Future<void> _save() async {
    if (_saving || !widget.enabled) return;
    final raw = _controller.text.trim();
    if (!AppSubscriptionPreference.isValidDate(raw)) {
      setState(() => _invalid = true);
      return;
    }
    setState(() {
      _invalid = false;
      _saving = true;
    });
    try {
      await widget.onEndsAtSave(raw);
      if (!mounted) return;
      setState(() => _expanded = false);
      _focusNode.unfocus();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _beginEdit() {
    if (!widget.enabled || _expanded) return;
    _controller.text = widget.endsAt;
    setState(() {
      _expanded = true;
      _invalid = false;
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _focusNode.requestFocus());
  }

  String _subtitleText(AppLocalizations l10n) {
    final raw = widget.endsAt.trim();
    if (raw.isEmpty) return widget.emptyLabel ?? l10n.commonUnlimited;
    return raw;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    final colors = context.appColors;
    final title = widget.title ?? l10n.adminSubscription;

    if (_expanded) {
      return AppPreferenceTile(
        title: title,
        icon: widget.icon,
        enabled: widget.enabled && !_saving,
        accentColor: widget.accentColor,
        subtitle: TextField(
          controller: _controller,
          focusNode: _focusNode,
          autofocus: true,
          keyboardType: TextInputType.datetime,
          inputFormatters: [
            FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
          ],
          textInputAction: TextInputAction.done,
          style: theme.textTheme.bodyMedium,
          decoration: kBorderlessInputDecoration.copyWith(
            hintText: l10n.adminDateFormatHint,
            errorText: _invalid ? l10n.adminInvalidDate : null,
          ),
          onSubmitted: (_) => _save(),
          onChanged: (_) {
            if (_invalid) setState(() => _invalid = false);
          },
        ),
        trailing: AppPreferenceInlineActions(
          onSave: _save,
          onCancel: _cancel,
          onGuardBlur: _guardBlur,
        ),
      );
    }

    return AppPreferenceTile(
      title: title,
      icon: widget.icon,
      enabled: widget.enabled,
      accentColor: widget.accentColor,
      subtitle: Text(
        _subtitleText(l10n),
        style: theme.textTheme.bodyMedium?.copyWith(
          color: widget.accentColor ??
              (widget.endsAt.trim().isEmpty ? colors.muted : null),
        ),
      ),
      trailing: const AppTrailingChevron(),
      onTap: _beginEdit,
    );
  }
}

/// Parse DD.MM.YY / DD.MM.YYYY as UTC midnight calendar date.
DateTime? parseSubscriptionDateUtc(String raw) {
  final trimmed = raw.trim();
  if (trimmed.isEmpty) return null;
  final parts = trimmed.split('.');
  if (parts.length != 3) return null;
  final day = int.tryParse(parts[0]);
  var month = int.tryParse(parts[1]);
  var year = int.tryParse(parts[2]);
  if (day == null || month == null || year == null) return null;
  if (year < 100) year += 2000;
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  final dt = DateTime.utc(year, month, day);
  if (dt.year != year || dt.month != month || dt.day != day) return null;
  return dt;
}

/// Parse DD.MM.YY / DD.MM.YYYY to ISO date string for API.
String? subscriptionDateToIso(String raw) {
  final dt = parseSubscriptionDateUtc(raw);
  if (dt == null) return null;
  final mm = dt.month.toString().padLeft(2, '0');
  final dd = dt.day.toString().padLeft(2, '0');
  return '${dt.year}-$mm-${dd}T00:00:00Z';
}

/// Format ISO/API date to DD.MM.YYYY for display.
String formatSubscriptionDate(String isoOrDate) {
  final raw = isoOrDate.trim();
  if (raw.isEmpty) return '';
  final datePart = raw.contains('T') ? raw.split('T').first : raw;
  final parts = datePart.split('-');
  if (parts.length != 3) return raw;
  return '${parts[2]}.${parts[1]}.${parts[0]}';
}

/// Add [months] to a display date (or today UTC if empty / past), return DD.MM.YYYY.
String addMonthsToSubscriptionDisplay(String displayDate, int months) {
  final now = DateTime.now().toUtc();
  final today = DateTime.utc(now.year, now.month, now.day);
  var base = parseSubscriptionDateUtc(displayDate) ?? today;
  if (base.isBefore(today)) base = today;
  final next = _addMonthsUtc(base, months);
  final dd = next.day.toString().padLeft(2, '0');
  final mm = next.month.toString().padLeft(2, '0');
  return '$dd.$mm.${next.year}';
}

DateTime _addMonthsUtc(DateTime base, int months) {
  final total = base.month - 1 + months;
  final year = base.year + total ~/ 12;
  final month = total % 12 + 1;
  final lastDay = DateTime.utc(year, month + 1, 0).day;
  final day = base.day > lastDay ? lastDay : base.day;
  return DateTime.utc(year, month, day);
}
