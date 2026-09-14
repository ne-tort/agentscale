import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:prodavan/core/auth/auth_http.dart';
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

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, dynamic>> me() async {
    final res = await AuthHttp.get(_uri('/me'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getSummary(String companyId) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/summary'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setCompanyPassword({
    required String companyId,
    required String password,
  }) async {
    final res = await AuthHttp.put(_uri('/companies/$companyId/password'), body: jsonEncode({'password': password}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listEmployees(String companyId) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/employees'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<List<Map<String, dynamic>>> listOrgCabinets(String companyId) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/cabinets'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<List<Map<String, dynamic>>> listCabinetAssignments({
    required String companyId,
    required String cabinetId,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/cabinets/$cabinetId/assignments'),
    );
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> assignCabinetEmployee({
    required String companyId,
    required String cabinetId,
    required String employeeId,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/cabinets/$cabinetId/assignments'),
      body: jsonEncode({'employee_id': employeeId}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> revokeCabinetAssignment({
    required String companyId,
    required String cabinetId,
    required String employeeId,
  }) async {
    final res = await AuthHttp.delete(
      _uri('/companies/$companyId/cabinets/$cabinetId/assignments/$employeeId'),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createEmployee({
    required String companyId,
    required String login,
    required String password,
    String? contactEmail,
    String? displayName,
    String role = 'member',
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/employees'),
      body: jsonEncode({
        'login': login,
        'password': password,
        if (contactEmail != null && contactEmail.isNotEmpty)
          'contact_email': contactEmail,
        if (displayName != null && displayName.isNotEmpty) 'display_name': displayName,
        'role': role,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setEmployeePassword({
    required String companyId,
    required String employeeId,
    required String password,
  }) async {
    final res = await AuthHttp.put(
      _uri('/companies/$companyId/employees/$employeeId/password'),
      body: jsonEncode({'password': password}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateEmployeeContactEmail({
    required String companyId,
    required String employeeId,
    String? contactEmail,
  }) async {
    final res = await AuthHttp.patch(
      _uri('/companies/$companyId/employees/$employeeId/contact-email'),
      body: jsonEncode({'contact_email': contactEmail}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> disableEmployee(String employeeId) async {
    final res = await AuthHttp.post(_uri('/employees/$employeeId/disable'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> enableEmployee(String employeeId) async {
    final res = await AuthHttp.post(_uri('/employees/$employeeId/enable'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createCabinet({
    required String companyId,
    required String name,
  }) async {
    final res = await AuthHttp.post(
      _uri('/cabinets'),
      body: jsonEncode({'name': name, 'company_id': companyId}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> copyCabinet({
    required String companyId,
    required String cabinetId,
    String? name,
  }) async {
    final payload = <String, dynamic>{'company_id': companyId};
    if (name != null && name.trim().isNotEmpty) {
      payload['name'] = name.trim();
    }
    final res = await AuthHttp.post(
      _uri('/cabinets/$cabinetId/copy'),
      body: jsonEncode(payload),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> deleteCabinet(String cabinetId) async {
    final res = await AuthHttp.delete(_uri('/cabinets/$cabinetId'));
    _throwIfError(res);
  }

  Future<List<Map<String, dynamic>>> listAiKeys(String companyId) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/ai-keys'));
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) return body.cast<Map<String, dynamic>>();
    return const [];
  }

  Future<Map<String, dynamic>> getAiKey({
    required String companyId,
    required String keyId,
  }) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/ai-keys/$keyId'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createAiKey({
    required String companyId,
    required String name,
    String provider = 'cursor',
    String apiKind = 'cursor_sdk',
    String? secret,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/ai-keys'),
      body: jsonEncode({
        'name': name,
        'provider': provider,
        'api_kind': apiKind,
        if (secret != null && secret.isNotEmpty) 'secret': secret,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> patchAiKey({
    required String companyId,
    required String keyId,
    String? status,
    String? name,
    String? provider,
    String? apiKind,
    String? nextRenewalAt,
    bool clearNextRenewalAt = false,
  }) async {
    final res = await AuthHttp.patch(
      _uri('/companies/$companyId/ai-keys/$keyId'),
      body: jsonEncode({
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

  Future<void> deleteAiKey({
    required String companyId,
    required String keyId,
  }) async {
    final res = await AuthHttp.delete(_uri('/companies/$companyId/ai-keys/$keyId'));
    _throwIfError(res);
  }

  Future<Map<String, dynamic>> renewAiKey({
    required String companyId,
    required String keyId,
    int months = 1,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/ai-keys/$keyId/renew'),
      body: jsonEncode({'months': months}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> rotateAiKeySecret({
    required String companyId,
    required String keyId,
    required String secret,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/ai-keys/$keyId/rotate-secret'),
      body: jsonEncode({'secret': secret}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listContainers({
    required String companyId,
    int limit = 200,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/containers').replace(queryParameters: {'limit': '$limit'}),
    );
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) return items.cast<Map<String, dynamic>>();
    return const [];
  }

  Future<Map<String, dynamic>> getContainer({
    required String companyId,
    required String projectId,
  }) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/containers/$projectId'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> pauseContainer({
    required String companyId,
    required String projectId,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/containers/$projectId/pause'),
      body: '{}',
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> resumeContainer({
    required String companyId,
    required String projectId,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/containers/$projectId/resume'),
      body: '{}',
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> reloadContainer({
    required String companyId,
    required String projectId,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/containers/$projectId/reload'),
      body: '{}',
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> listWorkspaceEntries({
    required String companyId,
    required String projectId,
    String path = '',
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/containers/$projectId/workspace/entries').replace(
        queryParameters: {'path': path},
      ),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> previewWorkspaceFile({
    required String companyId,
    required String projectId,
    required String path,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/containers/$projectId/workspace/preview').replace(
        queryParameters: {'path': path},
      ),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Uint8List> downloadWorkspaceFile({
    required String companyId,
    required String projectId,
    required String path,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/containers/$projectId/workspace/content').replace(
        queryParameters: {'path': path},
      ),
    );
    _throwIfError(res);
    return res.bodyBytes;
  }

  Future<void> deleteContainer({
    required String companyId,
    required String projectId,
  }) async {
    final res = await AuthHttp.delete(_uri('/companies/$companyId/containers/$projectId'));
    _throwIfError(res);
  }

  Future<List<Map<String, dynamic>>> listModules(String companyId) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/modules'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) return items.cast<Map<String, dynamic>>();
    return const [];
  }

  Future<Map<String, dynamic>> getModule({
    required String companyId,
    required String moduleId,
  }) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/modules/$moduleId'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listModuleProjects({
    required String companyId,
    required String moduleId,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/modules/$moduleId/projects'),
    );
    _throwIfError(res);
    final decoded = jsonDecode(res.body);
    if (decoded is Map && decoded['items'] is List) {
      return [
        for (final item in decoded['items'] as List)
          if (item is Map) Map<String, dynamic>.from(item),
      ];
    }
    return const [];
  }

  Future<List<Map<String, dynamic>>> listModuleCabinets({
    required String companyId,
    required String moduleId,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/modules/$moduleId/cabinets'),
    );
    _throwIfError(res);
    final decoded = jsonDecode(res.body);
    if (decoded is Map && decoded['items'] is List) {
      return [
        for (final item in decoded['items'] as List)
          if (item is Map) Map<String, dynamic>.from(item),
      ];
    }
    return const [];
  }

  Future<Map<String, dynamic>> createModule({
    required String companyId,
    required String name,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/modules'),
      body: jsonEncode({'name': name}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateModule({
    required String companyId,
    required String moduleId,
    String? name,
    List<String>? cabinetIds,
  }) async {
    final payload = <String, dynamic>{};
    if (name != null) payload['name'] = name;
    if (cabinetIds != null) payload['cabinet_ids'] = cabinetIds;
    final res = await AuthHttp.patch(
      _uri('/companies/$companyId/modules/$moduleId'),
      body: jsonEncode(payload),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> deleteModule({
    required String companyId,
    required String moduleId,
  }) async {
    final res = await AuthHttp.delete(_uri('/companies/$companyId/modules/$moduleId'));
    _throwIfError(res);
  }

  Future<Map<String, dynamic>> copyModule({
    required String companyId,
    required String moduleId,
    String? name,
  }) async {
    final payload = <String, dynamic>{};
    if (name != null && name.trim().isNotEmpty) {
      payload['name'] = name.trim();
    }
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/modules/$moduleId/copy'),
      body: jsonEncode(payload),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<String>> listModuleMetaSlugs({
    required String companyId,
    required String moduleId,
  }) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/modules/$moduleId/meta/documents'));
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is! List) return const [];
    return items.map((e) => (e as Map)['slug'] as String).toList();
  }

  Future<Map<String, dynamic>> getModuleMetaDocument({
    required String companyId,
    required String moduleId,
    required String slug,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/modules/$moduleId/meta/documents/$slug'),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> putModuleMetaDocument({
    required String companyId,
    required String moduleId,
    required String slug,
    required Object body,
  }) async {
    final res = await AuthHttp.put(
      _uri('/companies/$companyId/modules/$moduleId/meta/documents/$slug'),
      body: jsonEncode({'body': body}),
    );
    _throwIfError(res);
  }

  Future<List<Map<String, dynamic>>> listModuleDataRows({
    required String companyId,
    required String moduleId,
    required String tableSlug,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/modules/$moduleId/data/$tableSlug'),
    );
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> createModuleDataRow({
    required String companyId,
    required String moduleId,
    required String tableSlug,
    required Map<String, dynamic> body,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/modules/$moduleId/data/$tableSlug'),
      body: jsonEncode({'body': body}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateModuleDataRow({
    required String companyId,
    required String moduleId,
    required String tableSlug,
    required String rowId,
    required Map<String, dynamic> body,
  }) async {
    final res = await AuthHttp.patch(
      _uri('/companies/$companyId/modules/$moduleId/data/$tableSlug/$rowId'),
      body: jsonEncode({'body': body}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> deleteModuleDataRow({
    required String companyId,
    required String moduleId,
    required String tableSlug,
    required String rowId,
  }) async {
    final res = await AuthHttp.delete(
      _uri('/companies/$companyId/modules/$moduleId/data/$tableSlug/$rowId'),
    );
    _throwIfError(res);
    if (res.body.isEmpty) return <String, dynamic>{'deleted': true};
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getAiKeyScopeBindings({
    required String companyId,
    required String keyId,
  }) async {
    final res = await AuthHttp.get(
      _uri('/companies/$companyId/ai-keys/$keyId/scope-bindings'),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> setAiKeyScopeBindings({
    required String companyId,
    required String keyId,
    required List<String> employeeIds,
    required List<String> cabinetIds,
    required List<String> projectIds,
  }) async {
    final res = await AuthHttp.put(
      _uri('/companies/$companyId/ai-keys/$keyId/scope-bindings'),
      body: jsonEncode({
        'employee_ids': employeeIds,
        'cabinet_ids': cabinetIds,
        'project_ids': projectIds,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listAiModels(String companyId) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/ai-models'));
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) return body.cast<Map<String, dynamic>>();
    return const [];
  }

  Future<Map<String, dynamic>> createAiModel({
    required String companyId,
    required String name,
    List<String> apiKinds = const [],
    double? inputPriceUsdPerMtok,
    double? outputPriceUsdPerMtok,
    int? maxContextTokens,
    String? publisher,
    String? releasedAt,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/ai-models'),
      body: jsonEncode({
        'name': name,
        'api_kinds': apiKinds,
        if (inputPriceUsdPerMtok != null) 'input_price_usd_per_mtok': inputPriceUsdPerMtok,
        if (outputPriceUsdPerMtok != null) 'output_price_usd_per_mtok': outputPriceUsdPerMtok,
        if (maxContextTokens != null) 'max_context_tokens': maxContextTokens,
        if (publisher != null) 'publisher': publisher,
        if (releasedAt != null) 'released_at': releasedAt,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> patchAiModel({
    required String companyId,
    required String modelId,
    String? name,
    List<String>? apiKinds,
    double? inputPriceUsdPerMtok,
    double? outputPriceUsdPerMtok,
    int? maxContextTokens,
    String? publisher,
    String? releasedAt,
  }) async {
    final res = await AuthHttp.patch(
      _uri('/companies/$companyId/ai-models/$modelId'),
      body: jsonEncode({
        if (name != null) 'name': name,
        if (apiKinds != null) 'api_kinds': apiKinds,
        if (inputPriceUsdPerMtok != null) 'input_price_usd_per_mtok': inputPriceUsdPerMtok,
        if (outputPriceUsdPerMtok != null) 'output_price_usd_per_mtok': outputPriceUsdPerMtok,
        if (maxContextTokens != null) 'max_context_tokens': maxContextTokens,
        if (publisher != null) 'publisher': publisher,
        if (releasedAt != null) 'released_at': releasedAt,
      }),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listAiKeyModels({
    required String companyId,
    required String keyId,
  }) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/ai-keys/$keyId/models'));
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) return body.cast<Map<String, dynamic>>();
    return const [];
  }

  Future<List<Map<String, dynamic>>> updateAiKeyModels({
    required String companyId,
    required String keyId,
    required List<Map<String, dynamic>> selections,
  }) async {
    final res = await AuthHttp.put(
      _uri('/companies/$companyId/ai-keys/$keyId/models'),
      body: jsonEncode({'selections': selections}),
    );
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) return body.cast<Map<String, dynamic>>();
    return const [];
  }

  Future<Map<String, dynamic>> listAiKeyModelsLive({
    required String companyId,
    required String keyId,
  }) async {
    final res = await AuthHttp.get(_uri('/companies/$companyId/ai-keys/$keyId/models/live'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> uploadModuleContent({
    required String companyId,
    required String moduleId,
    required String filename,
    required List<int> bytes,
    String? mime,
  }) async {
    final req = http.MultipartRequest(
      'POST',
      _uri('/companies/$companyId/modules/$moduleId/content/upload'),
    );
    final h = await AuthHttp.headers();
    h.remove('Content-Type');
    req.headers.addAll(h);
    req.files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
    final streamed = await req.send();
    final res = await http.Response.fromStream(streamed);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> uploadModuleSecret({
    required String companyId,
    required String moduleId,
    required String secret,
    String? label,
  }) async {
    final res = await AuthHttp.post(
      _uri('/companies/$companyId/modules/$moduleId/secrets/upload'),
      body: jsonEncode({
        'secret': secret,
        if (label != null && label.isNotEmpty) 'label': label,
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
