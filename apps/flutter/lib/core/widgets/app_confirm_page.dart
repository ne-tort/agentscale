import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';

/// Full-screen confirm (Hiddify `ConfirmActionPage`): outlined status banner +
/// accent confirm tile. No modal dialogs.
class AppConfirmPage extends StatefulWidget {
  const AppConfirmPage({
    super.key,
    required this.title,
    required this.message,
    required this.confirmLabel,
    this.severity = AppStatusSeverity.warning,
    this.onConfirm,
  });

  final String title;
  final String message;
  final String confirmLabel;
  final AppStatusSeverity severity;

  /// When set, runs before pop(true). Busy spinner while awaiting.
  final Future<void> Function()? onConfirm;

  /// Returns `true` if the user confirmed (and [onConfirm], if any, finished).
  static Future<bool> push(
    BuildContext context, {
    required String title,
    required String message,
    required String confirmLabel,
    AppStatusSeverity severity = AppStatusSeverity.warning,
    Future<void> Function()? onConfirm,
  }) async {
    final result = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => AppConfirmPage(
          title: title,
          message: message,
          confirmLabel: confirmLabel,
          severity: severity,
          onConfirm: onConfirm,
        ),
      ),
    );
    return result ?? false;
  }

  @override
  State<AppConfirmPage> createState() => _AppConfirmPageState();
}

class _AppConfirmPageState extends State<AppConfirmPage> {
  bool _busy = false;

  bool get _destructive =>
      widget.severity == AppStatusSeverity.error ||
      widget.severity == AppStatusSeverity.critical;

  Color _accent(BuildContext context) {
    final tokens = context.appColors;
    return _destructive ? tokens.danger : tokens.warning;
  }

  IconData get _confirmIcon => _destructive
      ? Icons.delete_forever_rounded
      : Icons.warning_amber_rounded;

  Future<void> _run() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final action = widget.onConfirm;
      if (action != null) await action();
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (_) {
      if (mounted) setState(() => _busy = false);
      rethrow;
    }
  }

  @override
  Widget build(BuildContext context) {
    final accent = _accent(context);
    return AppScaffold(
      title: Text(widget.title),
      body: ListView(
        children: [
          AppStatusBanner(
            severity: widget.severity,
            message: widget.message,
            outlined: true,
          ),
          if (!_busy)
            AppNavPreference(
              title: widget.confirmLabel,
              icon: _confirmIcon,
              accentColor: accent,
              onTap: _run,
            )
          else
            const Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator()),
            ),
        ],
      ),
    );
  }
}
