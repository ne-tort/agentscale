import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Date preference (DD.MM.YY / DD.MM.YYYY). Empty = [emptyLabel] (default unlimited).
class AppSubscriptionPreference extends StatefulWidget {
  const AppSubscriptionPreference({
    super.key,
    required this.endsAt,
    required this.onEndsAtSave,
    this.enabled = true,
    this.title,
    this.emptyLabel,
    this.icon = Icons.event_rounded,
  });

  final String endsAt;
  final Future<void> Function(String endsAt) onEndsAtSave;
  final bool enabled;
  final String? title;
  final String? emptyLabel;
  final IconData icon;

  static final _datePattern = RegExp(r'^\d{2}\.\d{2}\.\d{2,4}$');

  static bool isValidDate(String raw) =>
      raw.isEmpty || _datePattern.hasMatch(raw.trim());

  @override
  State<AppSubscriptionPreference> createState() => _AppSubscriptionPreferenceState();
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
    final title = widget.title ?? l10n.adminEndsAt;

    if (_expanded) {
      return AppPreferenceTile(
        title: title,
        icon: widget.icon,
        enabled: widget.enabled && !_saving,
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
      subtitle: Text(
        _subtitleText(l10n),
        style: theme.textTheme.bodyMedium?.copyWith(
          color: widget.endsAt.trim().isEmpty ? colors.muted : null,
        ),
      ),
      trailing: const Icon(Icons.chevron_right_rounded, size: 22),
      onTap: _beginEdit,
    );
  }
}

/// Parse DD.MM.YY / DD.MM.YYYY to ISO date string for API.
String? subscriptionDateToIso(String raw) {
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
  final mm = month.toString().padLeft(2, '0');
  final dd = day.toString().padLeft(2, '0');
  return '$year-$mm-${dd}T00:00:00Z';
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
