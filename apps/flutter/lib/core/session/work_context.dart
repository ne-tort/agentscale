import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/prodavan_api.dart';

/// Dev session holder — OIDC/AppAuth replaces in production (L01/L05).
class WorkContext extends ChangeNotifier {
  String baseUrl = 'http://127.0.0.1:8000/api/v1';
  String bearerToken = '';
  String? cabinetId;
  String? projectId;
  String? companyId;

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
    notifyListeners();
  }

  void enterProject(String id) {
    projectId = id;
    notifyListeners();
  }

  void clear() {
    bearerToken = '';
    cabinetId = null;
    projectId = null;
    companyId = null;
    notifyListeners();
  }
}

final workContext = WorkContext();
