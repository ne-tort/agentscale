import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/config/api_base.dart';

/// Session holder — Bearer + work headers (L01/L05).
class WorkContext extends ChangeNotifier {
  /// Resolved at runtime (same-origin on web when API_BASE unset).
  static String get defaultBaseUrl => ApiBase.value;

  String baseUrl = ApiBase.value;
  String bearerToken = '';
  String? cabinetId;
  String? projectId;
  String? companyId;
  /// Per-employee selected project in the current cabinet (sidebar context).
  String? selectedProjectId;

  /// Active agent chat for chat-scoped module UI (persists while browsing hubs).
  String? selectedSessionId;

  /// Bumped when project launch/pause/resume/reload changes chat availability
  /// so [CabinetShell] can refresh `new_chat_enabled` without leaving the page.
  int projectLifecycleEpoch = 0;

  bool get isAuthenticated => bearerToken.isNotEmpty;

  ProdavanApi get api => ProdavanApi(
        baseUrl: baseUrl,
        bearerToken: bearerToken,
        cabinetId: cabinetId,
        projectId: projectId,
      );

  void setSession({
    required String baseUrl,
    required String bearerToken,
  }) {
    this.baseUrl = baseUrl;
    this.bearerToken = bearerToken;
    notifyListeners();
  }

  void enterCabinet(String id) {
    cabinetId = id;
    projectId = null;
    selectedProjectId = null;
    selectedSessionId = null;
    notifyListeners();
  }

  void enterProject(String id) {
    projectId = id;
    notifyListeners();
  }

  void setSelectedProjectId(String? id) {
    if (selectedProjectId == id) return;
    selectedProjectId = id;
    selectedSessionId = null;
    notifyListeners();
  }

  void setSelectedSessionId(String? id) {
    final next = (id == null || id.trim().isEmpty) ? null : id.trim();
    if (selectedSessionId == next) return;
    selectedSessionId = next;
    notifyListeners();
  }

  Future<void> loadProjectSelection(String cabinetId) async {
    final body = await api.getProjectSelection(cabinetId);
    setSelectedProjectId(body['project_id'] as String?);
  }

  Future<void> selectProject({
    required String cabinetId,
    required String? projectId,
  }) async {
    setSelectedProjectId(projectId);
    await api.putProjectSelection(cabinetId: cabinetId, projectId: projectId);
  }

  /// Project status / container settled — refresh chats rail enablement.
  void notifyProjectLifecycleChanged() {
    projectLifecycleEpoch++;
    notifyListeners();
  }

  void clear() {
    bearerToken = '';
    cabinetId = null;
    projectId = null;
    companyId = null;
    selectedProjectId = null;
    selectedSessionId = null;
    projectLifecycleEpoch = 0;
    notifyListeners();
  }
}

final workContext = WorkContext();
