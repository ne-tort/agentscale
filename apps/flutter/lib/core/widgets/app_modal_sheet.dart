import 'package:flutter/material.dart';

/// Shared modal bottom-sheet host.
///
/// CI gates raw `showModalBottomSheet` calls out of `lib/features` (modal
/// entity pickers must be full pages — see `AppSelectorPage`); feature-level
/// sheets go through this core primitive instead.
Future<T?> showAppModalSheet<T>({
  required BuildContext context,
  required WidgetBuilder builder,
  Color? backgroundColor,
  ShapeBorder? shape,
  BoxConstraints? constraints,
  bool isScrollControlled = true,
  bool useSafeArea = true,
}) {
  return showModalBottomSheet<T>(
    context: context,
    isScrollControlled: isScrollControlled,
    useSafeArea: useSafeArea,
    backgroundColor: backgroundColor,
    shape: shape,
    constraints: constraints,
    builder: builder,
  );
}
