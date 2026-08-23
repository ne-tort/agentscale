import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/company_api.dart';

/// Dev session holder for Company admin contour (L04).
class CompanyContext extends ChangeNotifier {
  String baseUrl = 'http://127.0.0.1:8000/api/v1';
  String bearerToken = '';
  String? companyId;
  String? companyName;

  bool get isAuthenticated => bearerToken.isNotEmpty && companyId != null;

  CompanyApi get api => CompanyApi(
        baseUrl: baseUrl,
        bearerToken: bearerToken,
        companyId: companyId,
      );

  void setSession({
    required String baseUrl,
    required String bearerToken,
  }) {
    this.baseUrl = baseUrl;
    this.bearerToken = bearerToken;
    notifyListeners();
  }

  void selectCompany({required String id, required String name}) {
    companyId = id;
    companyName = name;
    notifyListeners();
  }

  void clear() {
    bearerToken = '';
    companyId = null;
    companyName = null;
    notifyListeners();
  }
}

final companyContext = CompanyContext();
