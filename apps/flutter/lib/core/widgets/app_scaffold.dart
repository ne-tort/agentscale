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

  /// When true, body+app bar are full-bleed (no content max-width). Prefer false.
  final bool expandBody;

  bool get _hasAppBar =>
      title != null || (actions != null && actions!.isNotEmpty) || bottom != null;

  @override
  Widget build(BuildContext context) {
    Widget? pageBody = body;
    if (centerBody && pageBody != null) {
      pageBody = Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: pageBody,
        ),
      );
    }

    final inner = Scaffold(
      primary: false,
      appBar: _hasAppBar ? AppBar(title: title, actions: actions, bottom: bottom) : null,
      body: pageBody,
      floatingActionButton: floatingActionButton,
    );

    final Widget chrome;
    if (expandBody) {
      chrome = inner;
    } else {
      chrome = Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: AppBreakpoints.contentMaxWidth),
          child: SizedBox(width: double.infinity, height: double.infinity, child: inner),
        ),
      );
    }

    return Scaffold(
      drawer: drawer,
      bottomNavigationBar: bottomNavigationBar,
      body: chrome,
    );
  }
}
