import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:prodavan/core/api/prodavan_api.dart';

/// Company admin contour API client (L04) — org employees, cabinets, summary.
class CompanyApi {
  CompanyApi({
    required this.baseUrl,
    required this.bearerToken,
    this.companyId,
  });

  final String baseUrl;
  String bearerToken;
  String? companyId;

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $bearerToken',
        'Content-Type': 'application/json',
      };

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, dynamic>> me() async {
    final res = await http.get(_uri('/me'), headers: _headers);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getSummary(String companyId) async {
    final res = await http.get(_uri('/companies/$companyId/summary'), headers: _headers);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setCompanyPassword({
    required String companyId,
    required String password,
  }) async {
    final res = await http.put(
      _uri('/companies/$companyId/password'),
      headers: _headers,
      body: jsonEncode({'password': password}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listEmployees(String companyId) async {
    final res = await http.get(_uri('/companies/$companyId/employees'), headers: _headers);
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<List<Map<String, dynamic>>> listOrgCabinets(String companyId) async {
    final res = await http.get(_uri('/companies/$companyId/cabinets'), headers: _headers);
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> inviteEmployee({
    required String companyId,
    required String email,
    String? displayName,
    String role = 'member',
  }) async {
    final res = await http.post(
      _uri('/companies/$companyId/employees'),
      headers: _headers,
      body: jsonEncode({
        'email': email,
        if (displayName != null && displayName.isNotEmpty) 'display_name': displayName,
        'role': role,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> disableEmployee(String employeeId) async {
    final res = await http.post(
      _uri('/employees/$employeeId/disable'),
      headers: _headers,
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  void _throwIfError(http.Response res) {
    if (res.statusCode >= 400) {
      throw ProdavanApiException(res.statusCode, res.body);
    }
  }
}
