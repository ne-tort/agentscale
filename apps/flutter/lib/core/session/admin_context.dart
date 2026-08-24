import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/admin_api.dart';
import 'package:prodavan/core/config/api_base.dart';

/// Dev session holder for Platform Admin contour (L04).
class AdminContext extends ChangeNotifier {
  String baseUrl = ApiBase.value;
  String bearerToken = '';

  bool get isAuthenticated => bearerToken.isNotEmpty;

  AdminApi get api => AdminApi(baseUrl: baseUrl, bearerToken: bearerToken);

  void setSession({
    required String baseUrl,
    required String bearerToken,
  }) {
    this.baseUrl = baseUrl;
    this.bearerToken = bearerToken;
    notifyListeners();
  }

  void clear() {
    bearerToken = '';
    notifyListeners();
  }
}

final adminContext = AdminContext();
