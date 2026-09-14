import 'package:flutter/widgets.dart';

import 'package:prodavan/core/api/prodavan_api.dart';

/// Where module content uploads are sent.
enum ModuleContentUploadKind { cabinet, platform, company }

typedef ModuleContentUploadFn = Future<Map<String, dynamic>> Function({
  required String filename,
  required List<int> bytes,
  String? mime,
});

typedef ModuleSecretUploadFn = Future<Map<String, dynamic>> Function({
  required String secret,
  String? label,
});

typedef ModuleActionInvokeFn = Future<Map<String, dynamic>> Function({
  required String actionId,
  String? rowId,
});

/// Inherited scope for live module interpreters (upload, API, project leaf).
class ModuleRuntimeScope extends InheritedWidget {
  const ModuleRuntimeScope({
    super.key,
    required this.moduleId,
    required this.uploadKind,
    required this.uploadContentFn,
    required this.uploadSecretFn,
    this.invokeActionFn,
    this.api,
    this.cabinetId,
    this.companyId,
    this.projectId,
    this.sessionId,
    required super.child,
  }) : assert(
          uploadKind != ModuleContentUploadKind.cabinet ||
              (cabinetId != null && cabinetId.length > 0),
          'cabinet upload requires cabinetId',
        ),
        assert(
          uploadKind != ModuleContentUploadKind.company ||
              (companyId != null && companyId.length > 0),
          'company upload requires companyId',
        );

  /// Cabinet-scoped live editor (employee).
  factory ModuleRuntimeScope.cabinet({
    Key? key,
    required String cabinetId,
    required String moduleId,
    required ProdavanApi api,
    String? projectId,
    String? sessionId,
    required Widget child,
  }) {
    return ModuleRuntimeScope(
      key: key,
      moduleId: moduleId,
      api: api,
      uploadKind: ModuleContentUploadKind.cabinet,
      cabinetId: cabinetId,
      projectId: projectId,
      sessionId: sessionId,
      uploadContentFn: ({
        required String filename,
        required List<int> bytes,
        String? mime,
      }) {
        return api.uploadCabinetContent(
          cabinetId: cabinetId,
          filename: filename,
          bytes: bytes,
          mime: mime,
        );
      },
      uploadSecretFn: ({
        required String secret,
        String? label,
      }) {
        return api.uploadCabinetModuleSecret(
          cabinetId: cabinetId,
          moduleId: moduleId,
          secret: secret,
          label: label,
        );
      },
      invokeActionFn: ({
        required String actionId,
        String? rowId,
      }) {
        return api.invokeModuleAction(
          cabinetId: cabinetId,
          moduleId: moduleId,
          actionId: actionId,
          rowId: rowId,
          projectId: projectId,
        );
      },
      child: child,
    );
  }

  /// Platform admin instance editor.
  factory ModuleRuntimeScope.platform({
    Key? key,
    required String moduleId,
    required ModuleContentUploadFn uploadContentFn,
    required ModuleSecretUploadFn uploadSecretFn,
    ModuleActionInvokeFn? invokeActionFn,
    required Widget child,
  }) {
    return ModuleRuntimeScope(
      key: key,
      moduleId: moduleId,
      uploadKind: ModuleContentUploadKind.platform,
      uploadContentFn: uploadContentFn,
      uploadSecretFn: uploadSecretFn,
      invokeActionFn: invokeActionFn,
      child: child,
    );
  }

  /// Company instance editor.
  factory ModuleRuntimeScope.company({
    Key? key,
    required String companyId,
    required String moduleId,
    required ModuleContentUploadFn uploadContentFn,
    required ModuleSecretUploadFn uploadSecretFn,
    ModuleActionInvokeFn? invokeActionFn,
    required Widget child,
  }) {
    return ModuleRuntimeScope(
      key: key,
      moduleId: moduleId,
      uploadKind: ModuleContentUploadKind.company,
      companyId: companyId,
      uploadContentFn: uploadContentFn,
      uploadSecretFn: uploadSecretFn,
      invokeActionFn: invokeActionFn,
      child: child,
    );
  }

  final ModuleContentUploadKind uploadKind;
  final ModuleContentUploadFn uploadContentFn;
  final ModuleSecretUploadFn uploadSecretFn;
  final ModuleActionInvokeFn? invokeActionFn;
  final String? cabinetId;
  final String? companyId;

  /// When set, data/actions use the project leaf instance (hubs).
  final String? projectId;

  /// Active agent chat — filters/stamps ``scope.chats=current`` tables.
  final String? sessionId;
  final String moduleId;

  /// Cabinet work API — only set for [ModuleContentUploadKind.cabinet].
  final ProdavanApi? api;

  static ModuleRuntimeScope? maybeOf(BuildContext context) {
    return context.dependOnInheritedWidgetOfExactType<ModuleRuntimeScope>();
  }

  Future<Map<String, dynamic>> uploadContent({
    required String filename,
    required List<int> bytes,
    String? mime,
  }) {
    return uploadContentFn(filename: filename, bytes: bytes, mime: mime);
  }

  Future<Map<String, dynamic>> uploadSecret({
    required String secret,
    String? label,
  }) {
    return uploadSecretFn(secret: secret, label: label);
  }

  Future<Map<String, dynamic>> invokeAction({
    required String actionId,
    String? rowId,
  }) {
    final fn = invokeActionFn;
    if (fn == null) {
      throw StateError('module action invoke requires runtime scope');
    }
    return fn(actionId: actionId, rowId: rowId);
  }

  @override
  bool updateShouldNotify(ModuleRuntimeScope oldWidget) {
    return uploadKind != oldWidget.uploadKind ||
        cabinetId != oldWidget.cabinetId ||
        companyId != oldWidget.companyId ||
        projectId != oldWidget.projectId ||
        sessionId != oldWidget.sessionId ||
        moduleId != oldWidget.moduleId ||
        api != oldWidget.api;
  }
}
