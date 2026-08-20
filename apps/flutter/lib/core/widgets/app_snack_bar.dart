import 'package:flutter/material.dart';

import 'package:prodavan/core/errors/ui_messenger.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_radii.dart';

/// Unified snack presentation. Use via [AppSnackHost] or [AppSnackBar.show].
abstract final class AppSnackBar {
  static void show(
    BuildContext context, {
    required String message,
    required UiMessageKind kind,
  }) {
    final colors = context.appColors;
    late final Color bg;
    late final Color fg;
    switch (kind) {
      case UiMessageKind.error:
        bg = colors.snackErrorBg;
        fg = colors.snackErrorFg;
      case UiMessageKind.success:
        bg = colors.snackSuccessBg;
        fg = colors.snackSuccessFg;
      case UiMessageKind.info:
        bg = colors.snackInfoBg;
        fg = colors.snackInfoFg;
    }

    final messenger = ScaffoldMessenger.of(context);
    messenger.clearSnackBars();
    messenger.showSnackBar(
      SnackBar(
        content: Text(message, style: TextStyle(color: fg)),
        backgroundColor: bg,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: AppRadii.borderMd),
      ),
    );
  }
}

/// Listens to [UiMessenger] and shows [AppSnackBar].
class AppSnackHost extends StatefulWidget {
  const AppSnackHost({
    super.key,
    required this.messenger,
    required this.child,
  });

  final UiMessenger messenger;
  final Widget child;

  @override
  State<AppSnackHost> createState() => _AppSnackHostState();
}

class _AppSnackHostState extends State<AppSnackHost> {
  @override
  void initState() {
    super.initState();
    widget.messenger.addListener(_onMessage);
  }

  @override
  void didUpdateWidget(covariant AppSnackHost oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.messenger != widget.messenger) {
      oldWidget.messenger.removeListener(_onMessage);
      widget.messenger.addListener(_onMessage);
    }
  }

  @override
  void dispose() {
    widget.messenger.removeListener(_onMessage);
    super.dispose();
  }

  void _onMessage() {
    final pending = widget.messenger.pending;
    if (pending == null || !mounted) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      AppSnackBar.show(context, message: pending.text, kind: pending.kind);
      widget.messenger.clearPending();
    });
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
