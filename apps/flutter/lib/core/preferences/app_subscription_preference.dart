import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Subscription end date + lifetime switch on one row (Hiddify-style).
///
/// Switch off = lifetime subscription; switch on = edit end date.
class AppSubscriptionPreference extends StatefulWidget {
  const AppSubscriptionPreference({
    super.key,
    required this.lifetime,
    required this.endsAt,
    required this.onLifetimeChanged,
    required this.onEndsAtSave,
    this.enabled = true,
  });

  final bool lifetime;
  final String endsAt;
  final Future<void> Function(bool lifetime) onLifetimeChanged;
  final Future<void> Function(String endsAt) onEndsAtSave;
  final bool enabled;

  @override
  State<AppSubscriptionPreference> createState() => _AppSubscriptionPreferenceState();
}

class _AppSubscriptionPreferenceState extends State<AppSubscriptionPreference> {
  late TextEditingController _controller;
  late FocusNode _focusNode;
  bool _expanded = false;
  bool _saving = false;
  bool _ignoreNextBlur = false;

  bool get _hasEndDate => !widget.lifetime;

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
      _controller.text = widget.endsAt;
    });
    _focusNode.unfocus();
  }

  Future<void> _save() async {
    if (_saving || !widget.enabled || widget.lifetime) return;
    final raw = _controller.text.trim();
    setState(() => _saving = true);
    try {
      await widget.onEndsAtSave(raw);
      if (!mounted) return;
      setState(() => _expanded = false);
      _focusNode.unfocus();
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _beginEdit() {
    if (!widget.enabled || widget.lifetime || _expanded) return;
    _controller.text = widget.endsAt;
    setState(() => _expanded = true);
    WidgetsBinding.instance.addPostFrameCallback((_) => _focusNode.requestFocus());
  }

  String _subtitleText(AppLocalizations l10n) {
    if (widget.lifetime) return l10n.adminLifetimeSubscription;
    final raw = widget.endsAt.trim();
    if (raw.isEmpty) return l10n.adminDateFormatHint;
    return raw;
  }

  Future<void> _onSwitchChanged(bool hasEndDate) async {
    _guardBlur();
    await widget.onLifetimeChanged(!hasEndDate);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    final colors = context.appColors;

    if (_expanded && _hasEndDate) {
      return AppPreferenceTile(
        title: l10n.adminEndsAt,
        icon: Icons.event_rounded,
        enabled: widget.enabled && !_saving,
        subtitle: TextField(
          controller: _controller,
          focusNode: _focusNode,
          autofocus: true,
          keyboardType: TextInputType.datetime,
          inputFormatters: [
            FilteringTextInputFormatter.allow(RegExp(r'[0-9\-]')),
          ],
          textInputAction: TextInputAction.done,
          style: theme.textTheme.bodyMedium,
          decoration: InputDecoration(
            isDense: true,
            isCollapsed: true,
            border: InputBorder.none,
            contentPadding: EdgeInsets.zero,
            hintText: l10n.adminDateFormatHint,
          ),
          onSubmitted: (_) => _save(),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            AppPreferenceInlineActions(
              onSave: _save,
              onCancel: _cancel,
              onGuardBlur: _guardBlur,
            ),
            Switch.adaptive(
              value: _hasEndDate,
              onChanged: widget.enabled ? _onSwitchChanged : null,
            ),
          ],
        ),
      );
    }

    return AppPreferenceTile(
      title: l10n.adminEndsAt,
      icon: Icons.event_rounded,
      enabled: widget.enabled,
      subtitle: Text(
        _subtitleText(l10n),
        style: theme.textTheme.bodyMedium?.copyWith(
          color: widget.lifetime || widget.endsAt.trim().isEmpty ? colors.muted : null,
        ),
      ),
      trailing: Switch.adaptive(
        value: _hasEndDate,
        onChanged: widget.enabled ? _onSwitchChanged : null,
      ),
      onTap: _hasEndDate ? _beginEdit : null,
    );
  }
}
