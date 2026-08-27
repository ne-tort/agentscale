import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:prodavan/core/auth/auth_http.dart';
import 'package:prodavan/core/api/prodavan_api.dart';

/// Platform Admin API client (L04) — separate from employee shell.
class AdminApi {
  AdminApi({
    required this.baseUrl,
    required this.bearerToken,
  });

  final String baseUrl;
  String bearerToken;

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, dynamic>> me() async {
    final res = await AuthHttp.get(_uri('/me'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listCompanies() async {
    final res = await AuthHttp.get(_uri('/admin/companies'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> getCompany(String companyId) async {
    final res = await AuthHttp.get(_uri('/admin/companies/$companyId'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> deleteCompany(String companyId) async {
    final res = await AuthHttp.delete(_uri('/admin/companies/$companyId'));
    _throwIfError(res);
  }

  Future<Map<String, dynamic>> patchCompany({
    required String companyId,
    String? name,
    String? description,
    bool patchDescription = false,
    String? contactEmail,
    bool patchContactEmail = false,
    String? phone,
    bool patchPhone = false,
  }) async {
    final payload = <String, dynamic>{};
    if (name != null) payload['name'] = name;
    if (patchDescription) payload['description'] = description;
    if (patchContactEmail) payload['contact_email'] = contactEmail;
    if (patchPhone) payload['phone'] = phone;
    final res = await AuthHttp.patch(_uri('/admin/companies/$companyId'), body: jsonEncode(payload),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createCompany({
    required String name,
    required String password,
    String? adminEmail,
    String? adminDisplayName,
    String? description,
  }) async {
    final res = await AuthHttp.post(_uri('/companies'), body: jsonEncode({
        'name': name,
        'password': password,
        if (adminEmail != null && adminEmail.isNotEmpty) 'admin_email': adminEmail,
        if (adminDisplayName != null && adminDisplayName.isNotEmpty)
          'admin_display_name': adminDisplayName,
        if (description != null && description.isNotEmpty) 'description': description,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setCompanyPassword({
    required String companyId,
    required String password,
  }) async {
    final res = await AuthHttp.put(_uri('/admin/companies/$companyId/password'), body: jsonEncode({'password': password}),
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
    final res = await AuthHttp.post(_uri('/companies/$companyId/employees'), body: jsonEncode({
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
    final res = await AuthHttp.put(_uri('/admin/companies/$companyId/subscription'), body: jsonEncode({
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
    final res = await AuthHttp.put(_uri('/admin/companies/$companyId/cabinet-quotas'), body: jsonEncode({
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
    final res = await AuthHttp.put(_uri('/admin/companies/$companyId/agent-policy'), body: jsonEncode({
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
    final res = await AuthHttp.post(_uri(path));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<({List<Map<String, dynamic>> items, List<Map<String, dynamic>> cascadePending})>
      listCompaniesMetrics() async {
    final res = await AuthHttp.get(_uri('/admin/metrics/companies'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    final pending = body['cascade_pending'];
    return (
      items: items is List ? items.cast<Map<String, dynamic>>() : const <Map<String, dynamic>>[],
      cascadePending:
          pending is List ? pending.cast<Map<String, dynamic>>() : const <Map<String, dynamic>>[],
    );
  }

  Future<List<Map<String, dynamic>>> listStarterBundles() async {
    final res = await AuthHttp.get(_uri('/admin/starter-bundles'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  /// P1 Project Containers read-model (Project proxy until Container ORM).
  Future<List<Map<String, dynamic>>> listContainers({int limit = 200}) async {
    final res = await AuthHttp.get(_uri('/admin/containers').replace(queryParameters: {'limit': '$limit'}));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> getContainer(String projectId) async {
    final res = await AuthHttp.get(_uri('/admin/containers/$projectId'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> pauseContainer(String projectId) async {
    final res = await AuthHttp.post(_uri('/admin/containers/$projectId/pause'), body: '{}',);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> resumeContainer(String projectId) async {
    final res = await AuthHttp.post(_uri('/admin/containers/$projectId/resume'), body: '{}',);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> deleteContainer(String projectId) async {
    final res = await AuthHttp.delete(_uri('/admin/containers/$projectId'));
    _throwIfError(res);
  }

  Future<List<Map<String, dynamic>>> listAiKeys() async {
    final res = await AuthHttp.get(_uri('/admin/ai-keys'));
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) {
      return body.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> getAiKey(String keyId) async {
    final res = await AuthHttp.get(_uri('/admin/ai-keys/$keyId'));
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
    final res = await AuthHttp.post(_uri('/admin/ai-keys'), body: jsonEncode({
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
    String? nextRenewalAt,
    bool clearNextRenewalAt = false,
  }) async {
    final res = await AuthHttp.patch(_uri('/admin/ai-keys/$keyId'), body: jsonEncode({
        if (status != null) 'status': status,
        if (name != null) 'name': name,
        if (provider != null) 'provider': provider,
        if (apiKind != null) 'api_kind': apiKind,
        if (clearNextRenewalAt) 'next_renewal_at': null,
        if (!clearNextRenewalAt && nextRenewalAt != null) 'next_renewal_at': nextRenewalAt,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listCatalogEntries(String catalogId) async {
    final res = await AuthHttp.get(_uri('/admin/catalogs/$catalogId/entries'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) return items.cast<Map<String, dynamic>>();
    return const [];
  }

  Future<Map<String, dynamic>> createCatalogEntry({
    required String catalogId,
    required String title,
    String? id,
    String? subtitle,
    String? iconName,
    Map<String, dynamic>? payload,
  }) async {
    final res = await AuthHttp.post(_uri('/admin/catalogs/$catalogId/entries'), body: jsonEncode({
        'title': title,
        if (id != null) 'id': id,
        if (subtitle != null) 'subtitle': subtitle,
        if (iconName != null) 'icon_name': iconName,
        if (payload != null) 'payload': payload,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> patchCatalogEntry({
    required String catalogId,
    required String entryId,
    String? title,
    String? subtitle,
    Map<String, dynamic>? payload,
  }) async {
    final res = await AuthHttp.patch(_uri('/admin/catalogs/$catalogId/entries/$entryId'), body: jsonEncode({
        if (title != null) 'title': title,
        if (subtitle != null) 'subtitle': subtitle,
        if (payload != null) 'payload': payload,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> deleteCatalogEntry({
    required String catalogId,
    required String entryId,
  }) async {
    final res = await AuthHttp.delete(_uri('/admin/catalogs/$catalogId/entries/$entryId'));
    _throwIfError(res);
  }

  Future<void> deleteAiKey(String keyId) async {
    final res = await AuthHttp.delete(_uri('/admin/ai-keys/$keyId'));
    _throwIfError(res);
  }

  Future<Map<String, dynamic>> renewAiKey({
    required String keyId,
    int months = 1,
  }) async {
    final res = await AuthHttp.post(_uri('/admin/ai-keys/$keyId/renew'), body: jsonEncode({'months': months}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> rotateAiKeySecret({
    required String keyId,
    required String secret,
  }) async {
    final res = await AuthHttp.post(_uri('/admin/ai-keys/$keyId/rotate-secret'), body: jsonEncode({'secret': secret}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setAiKeyCompanies({
    required String keyId,
    required List<String> companyIds,
  }) async {
    final res = await AuthHttp.put(_uri('/admin/ai-keys/$keyId/companies'), body: jsonEncode({'company_ids': companyIds}),
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
    final res = await AuthHttp.get(_uri('/admin/platform-events').replace(queryParameters: params));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<List<Map<String, dynamic>>> listCabinets() async {
    final res = await AuthHttp.get(_uri('/admin/cabinets'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> createCabinet({
    required String name,
    String? companyId,
    List<String>? companyIds,
  }) async {
    final payload = <String, dynamic>{'name': name};
    if (companyIds != null && companyIds.isNotEmpty) {
      payload['company_ids'] = companyIds;
    } else if (companyId != null) {
      payload['company_id'] = companyId;
    }
    final res = await AuthHttp.post(
      _uri('/admin/cabinets'),
      body: jsonEncode(payload),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getCabinet(String cabinetId) async {
    final res = await AuthHttp.get(_uri('/admin/cabinets/$cabinetId'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateCabinet({
    required String cabinetId,
    String? name,
    String? companyId,
    List<String>? companyIds,
  }) async {
    final payload = <String, dynamic>{};
    if (name != null) payload['name'] = name;
    if (companyId != null) payload['company_id'] = companyId;
    if (companyIds != null) payload['company_ids'] = companyIds;
    final res = await AuthHttp.patch(
      _uri('/admin/cabinets/$cabinetId'),
      body: jsonEncode(payload),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> deleteCabinet(String cabinetId) async {
    final res = await AuthHttp.delete(_uri('/admin/cabinets/$cabinetId'));
    _throwIfError(res);
  }

  Future<List<Map<String, dynamic>>> listModules() async {
    final res = await AuthHttp.get(_uri('/admin/modules'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> createModule({required String name}) async {
    final res = await AuthHttp.post(
      _uri('/admin/modules'),
      body: jsonEncode({'name': name}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getModule(String moduleId) async {
    final res = await AuthHttp.get(_uri('/admin/modules/$moduleId'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateModule({
    required String moduleId,
    String? name,
    List<String>? companyIds,
  }) async {
    final payload = <String, dynamic>{};
    if (name != null) payload['name'] = name;
    if (companyIds != null) payload['company_ids'] = companyIds;
    final res = await AuthHttp.patch(
      _uri('/admin/modules/$moduleId'),
      body: jsonEncode(payload),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> deleteModule(String moduleId) async {
    final res = await AuthHttp.delete(_uri('/admin/modules/$moduleId'));
    _throwIfError(res);
  }

  Future<List<String>> listModuleMetaSlugs(String moduleId) async {
    final res = await AuthHttp.get(_uri('/admin/modules/$moduleId/meta/documents'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items
          .map((e) => e is Map ? e['slug'] as String? : null)
          .whereType<String>()
          .toList();
    }
    return const [];
  }

  Future<Map<String, dynamic>> getModuleMetaDocument({
    required String moduleId,
    required String slug,
  }) async {
    final res = await AuthHttp.get(_uri('/admin/modules/$moduleId/meta/documents/$slug'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> putModuleMetaDocument({
    required String moduleId,
    required String slug,
    required Object body,
  }) async {
    final res = await AuthHttp.put(
      _uri('/admin/modules/$moduleId/meta/documents/$slug'),
      body: jsonEncode({'body': body}),
    );
    _throwIfError(res);
  }

  Future<void> deleteModuleMetaDocument({
    required String moduleId,
    required String slug,
  }) async {
    final res = await AuthHttp.delete(_uri('/admin/modules/$moduleId/meta/documents/$slug'));
    _throwIfError(res);
  }

  Future<Map<String, dynamic>> drainTriggers({
    int maxProjects = 20,
    int maxPerProject = 10,
  }) async {
    final res = await AuthHttp.post(
      _uri('/admin/triggers/drain').replace(queryParameters: {
        'max_projects': '$maxProjects',
        'max_per_project': '$maxPerProject',
      }),
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
