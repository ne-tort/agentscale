import 'package:flutter/widgets.dart';

import 'package:prodavan/core/api/prodavan_api.dart';

/// Inherited scope for live cabinet/project module interpreters (upload, API).
class ModuleRuntimeScope extends InheritedWidget {
  const ModuleRuntimeScope({
    super.key,
    required this.cabinetId,
    required this.moduleId,
    required this.api,
    this.projectId,
    required super.child,
  });

  final String cabinetId;
  /// When set, data/actions use the project leaf instance (hubs).
  final String? projectId;
  final String moduleId;
  final ProdavanApi api;

  static ModuleRuntimeScope? maybeOf(BuildContext context) {
    return context.dependOnInheritedWidgetOfExactType<ModuleRuntimeScope>();
  }

  @override
  bool updateShouldNotify(ModuleRuntimeScope oldWidget) {
    return cabinetId != oldWidget.cabinetId ||
        projectId != oldWidget.projectId ||
        moduleId != oldWidget.moduleId ||
        api != oldWidget.api;
  }
}
