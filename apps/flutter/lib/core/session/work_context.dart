import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/prodavan_api.dart';

/// Session holder — Bearer + work headers (L01/L05).
class WorkContext extends ChangeNotifier {
  /// Overridden at build time for k3s web: `--dart-define=API_BASE=http://prodavan.local:8088/api/v1`.
  static const String defaultBaseUrl = String.fromEnvironment(
    'API_BASE',
    defaultValue: 'http://127.0.0.1:8000/api/v1',
  );

  String baseUrl = defaultBaseUrl;
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

  int _cabinetMetaEpoch = 0;
  int get cabinetMetaEpoch => _cabinetMetaEpoch;

  void notifyCabinetMetaChanged() {
    _cabinetMetaEpoch += 1;
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
