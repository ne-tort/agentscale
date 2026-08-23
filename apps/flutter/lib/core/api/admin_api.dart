import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:prodavan/core/api/prodavan_api.dart';

/// Platform Admin API client (L04) — separate from employee shell.
class AdminApi {
  AdminApi({
    required this.baseUrl,
    required this.bearerToken,
  });

  final String baseUrl;
  String bearerToken;

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

  Future<List<Map<String, dynamic>>> listCompanies() async {
    final res = await http.get(_uri('/admin/companies'), headers: _headers);
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> getCompany(String companyId) async {
    final res = await http.get(_uri('/admin/companies/$companyId'), headers: _headers);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createCompany({
    required String name,
    required String adminEmail,
    String? adminDisplayName,
  }) async {
    final res = await http.post(
      _uri('/companies'),
      headers: _headers,
      body: jsonEncode({
        'name': name,
        'admin_email': adminEmail,
        if (adminDisplayName != null && adminDisplayName.isNotEmpty)
          'admin_display_name': adminDisplayName,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setCabinetQuotas({
    required String companyId,
    required int maxCabinets,
    required int maxPackagesPerCabinet,
    required int maxBundleImportMb,
  }) async {
    final res = await http.put(
      _uri('/admin/companies/$companyId/cabinet-quotas'),
      headers: _headers,
      body: jsonEncode({
        'max_cabinets': maxCabinets,
        'max_packages_per_cabinet': maxPackagesPerCabinet,
        'max_bundle_import_mb': maxBundleImportMb,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setAgentPolicy({
    required String companyId,
    required String toolPreset,
    String? preferredProvider,
    required bool platformFallback,
    List<String> modelAllowlist = const [],
    int? maxAgentTokensMonth,
    int? maxTokensPerRun,
  }) async {
    final res = await http.put(
      _uri('/admin/companies/$companyId/agent-policy'),
      headers: _headers,
      body: jsonEncode({
        'tool_preset': toolPreset,
        'preferred_provider': preferredProvider,
        'platform_fallback': platformFallback,
        'model_allowlist': modelAllowlist,
        'max_agent_tokens_month': maxAgentTokensMonth,
        'max_tokens_per_run': maxTokensPerRun,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listCompaniesMetrics() async {
    final res = await http.get(_uri('/admin/metrics/companies'), headers: _headers);
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<List<Map<String, dynamic>>> listAiKeys() async {
    final res = await http.get(_uri('/admin/ai-keys'), headers: _headers);
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) {
      return body.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> getAiKey(String keyId) async {
    final res = await http.get(_uri('/admin/ai-keys/$keyId'), headers: _headers);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createAiKey({
    required String name,
    required String provider,
    required String apiKind,
    required String secret,
    List<String> companyIds = const [],
  }) async {
    final res = await http.post(
      _uri('/admin/ai-keys'),
      headers: _headers,
      body: jsonEncode({
        'name': name,
        'provider': provider,
        'api_kind': apiKind,
        'secret': secret,
        'company_ids': companyIds,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> patchAiKey({
    required String keyId,
    String? status,
    String? name,
  }) async {
    final res = await http.patch(
      _uri('/admin/ai-keys/$keyId'),
      headers: _headers,
      body: jsonEncode({
        if (status != null) 'status': status,
        if (name != null) 'name': name,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> renewAiKey({
    required String keyId,
    int months = 1,
  }) async {
    final res = await http.post(
      _uri('/admin/ai-keys/$keyId/renew'),
      headers: _headers,
      body: jsonEncode({'months': months}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> rotateAiKeySecret({
    required String keyId,
    required String secret,
  }) async {
    final res = await http.post(
      _uri('/admin/ai-keys/$keyId/rotate-secret'),
      headers: _headers,
      body: jsonEncode({'secret': secret}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setAiKeyCompanies({
    required String keyId,
    required List<String> companyIds,
  }) async {
    final res = await http.put(
      _uri('/admin/ai-keys/$keyId/companies'),
      headers: _headers,
      body: jsonEncode({'company_ids': companyIds}),
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
