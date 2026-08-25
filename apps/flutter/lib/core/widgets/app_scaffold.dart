import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

class AppScaffold extends StatelessWidget {
  const AppScaffold({
    super.key,
    this.title,
    this.actions,
    this.body,
    this.floatingActionButton,
    this.drawer,
    this.bottomNavigationBar,
    this.bottom,
    this.centerBody = false,
    this.expandBody = false,
  });

  final Widget? title;
  final List<Widget>? actions;
  final Widget? body;
  final Widget? floatingActionButton;
  final Widget? drawer;
  final Widget? bottomNavigationBar;
  final PreferredSizeWidget? bottom;
  final bool centerBody;

  /// When true, body is full-bleed (no content max-width). Prefer false.
  final bool expandBody;

  @override
  Widget build(BuildContext context) {
    Widget? content = body;
    if (centerBody && content != null) {
      content = Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: content,
        ),
      );
    }
    if (!expandBody && content != null) {
      content = Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: AppBreakpoints.contentMaxWidth),
          child: SizedBox(width: double.infinity, child: content),
        ),
      );
    }
    return Scaffold(
      appBar: title == null && (actions == null || actions!.isEmpty)
          ? null
          : AppBar(title: title, actions: actions, bottom: bottom),
      body: content,
      floatingActionButton: floatingActionButton,
      drawer: drawer,
      bottomNavigationBar: bottomNavigationBar,
    );
  }
}
