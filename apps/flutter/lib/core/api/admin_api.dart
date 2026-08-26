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

  Future<Map<String, dynamic>> patchCompany({
    required String companyId,
    String? name,
    String? description,
    bool patchDescription = false,
  }) async {
    final payload = <String, dynamic>{};
    if (name != null) payload['name'] = name;
    if (patchDescription) payload['description'] = description;
    final res = await http.patch(
      _uri('/admin/companies/$companyId'),
      headers: _headers,
      body: jsonEncode(payload),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createCompany({
    required String name,
    String? adminEmail,
    String? adminDisplayName,
    String? description,
  }) async {
    final res = await http.post(
      _uri('/companies'),
      headers: _headers,
      body: jsonEncode({
        'name': name,
        if (adminEmail != null && adminEmail.isNotEmpty) 'admin_email': adminEmail,
        if (adminDisplayName != null && adminDisplayName.isNotEmpty)
          'admin_display_name': adminDisplayName,
        if (description != null && description.isNotEmpty) 'description': description,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
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

  Future<Map<String, dynamic>> setCompanySubscription({
    required String companyId,
    required bool subscriptionLifetime,
    String? subscriptionEndsAt,
  }) async {
    final res = await http.put(
      _uri('/admin/companies/$companyId/subscription'),
      headers: _headers,
      body: jsonEncode({
        'subscription_lifetime': subscriptionLifetime,
        if (subscriptionEndsAt != null && subscriptionEndsAt.isNotEmpty)
          'subscription_ends_at': subscriptionEndsAt,
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
    List<String>? modelAllowlist,
    int? maxAgentTokensMonth,
    int? maxTokensPerRun,
    double? maxCostUsdMonth,
    int maxAttachmentMb = 20,
    int? idlePauseAfterHours,
    String? webhookHmacSecret,
    String? telegramHmacSecret,
  }) async {
    final res = await http.put(
      _uri('/admin/companies/$companyId/agent-policy'),
      headers: _headers,
      body: jsonEncode({
        'tool_preset': toolPreset,
        'preferred_provider': preferredProvider,
        'platform_fallback': platformFallback,
        // Always send explicit list (incl. empty) so UI can clear; callers must pass loaded value.
        'model_allowlist': modelAllowlist ?? const <String>[],
        'max_agent_tokens_month': maxAgentTokensMonth,
        'max_tokens_per_run': maxTokensPerRun,
        'max_cost_usd_month': maxCostUsdMonth,
        'max_attachment_mb': maxAttachmentMb,
        'idle_pause_after_hours': idlePauseAfterHours,
        if (webhookHmacSecret != null) 'webhook_hmac_secret': webhookHmacSecret,
        if (telegramHmacSecret != null) 'telegram_hmac_secret': telegramHmacSecret,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> sweepIdlePause({String? companyId}) async {
    final path = companyId == null || companyId.isEmpty
        ? '/admin/triggers/idle-pause/sweep'
        : '/admin/companies/$companyId/idle-pause/sweep';
    final res = await http.post(_uri(path), headers: _headers);
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

  Future<List<Map<String, dynamic>>> listStarterBundles() async {
    final res = await http.get(_uri('/admin/starter-bundles'), headers: _headers);
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
    String provider = 'cursor',
    String apiKind = 'cursor_sdk',
    String? secret,
    List<String> companyIds = const [],
  }) async {
    final res = await http.post(
      _uri('/admin/ai-keys'),
      headers: _headers,
      body: jsonEncode({
        'name': name,
        'provider': provider,
        'api_kind': apiKind,
        if (secret != null && secret.isNotEmpty) 'secret': secret,
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
    String? provider,
    String? apiKind,
  }) async {
    final res = await http.patch(
      _uri('/admin/ai-keys/$keyId'),
      headers: _headers,
      body: jsonEncode({
        if (status != null) 'status': status,
        if (name != null) 'name': name,
        if (provider != null) 'provider': provider,
        if (apiKind != null) 'api_kind': apiKind,
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

  Future<List<Map<String, dynamic>>> listPlatformEvents({
    String? companyId,
    String? eventType,
    int limit = 50,
  }) async {
    final params = <String, String>{
      'limit': '$limit',
      if (companyId != null && companyId.isNotEmpty) 'company_id': companyId,
      if (eventType != null && eventType.isNotEmpty) 'event_type': eventType,
    };
    final res = await http.get(
      _uri('/admin/platform-events').replace(queryParameters: params),
      headers: _headers,
    );
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> drainTriggers({
    int maxProjects = 20,
    int maxPerProject = 10,
  }) async {
    final res = await http.post(
      _uri('/admin/triggers/drain').replace(queryParameters: {
        'max_projects': '$maxProjects',
        'max_per_project': '$maxPerProject',
      }),
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
