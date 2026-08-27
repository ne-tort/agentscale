import 'dart:convert';

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

  Future<Map<String, dynamic>> disableEmployee(String employeeId) async {
    final res = await AuthHttp.post(_uri('/employees/$employeeId/disable'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
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

  void _throwIfError(http.Response res) {
    if (res.statusCode >= 400) {
      throw ProdavanApiException(res.statusCode, res.body);
    }
  }
}
