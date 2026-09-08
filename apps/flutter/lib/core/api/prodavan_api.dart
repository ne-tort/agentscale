import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:prodavan/core/auth/auth_http.dart';

/// Minimal Prodavan API client (L05) — Bearer + work context headers.
class ProdavanApi {
  ProdavanApi({
    required this.baseUrl,
    required this.bearerToken,
    this.cabinetId,
    this.projectId,
  });

  final String baseUrl;
  String bearerToken;
  String? cabinetId;
  String? projectId;

  Map<String, String> get _workHeaders => {
        if (cabinetId != null) 'X-Cabinet-Id': cabinetId!,
        if (projectId != null) 'X-Project-Id': projectId!,
      };

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, dynamic>> me() async {
    final res = await AuthHttp.get(_uri('/me'), extraHeaders: _workHeaders);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listCabinets() async {
    final res = await AuthHttp.get(_uri('/cabinets'), extraHeaders: _workHeaders);
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) {
      return body.cast<Map<String, dynamic>>();
    }
    if (body is Map<String, dynamic>) {
      final items = body['items'];
      if (items is List) {
        return items.cast<Map<String, dynamic>>();
      }
    }
    return const [];
  }

  Future<Map<String, dynamic>> createCabinet({
    required String name,
    required String companyId,
  }) async {
    final res = await AuthHttp.post(
      _uri('/cabinets'),
      body: jsonEncode({'name': name, 'company_id': companyId}),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getCabinet(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.get(_uri('/cabinets/$cabinetId'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> renameCabinet({
    required String cabinetId,
    required String name,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.patch(
        _uri('/cabinets/$cabinetId'),
        body: jsonEncode({'name': name}),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> archiveCabinet(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.post(
        _uri('/cabinets/$cabinetId/archive'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<void> deleteCabinet(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.delete(
        _uri('/cabinets/$cabinetId'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<List<Map<String, dynamic>>> listCabinetModules(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.get(
        _uri('/cabinets/$cabinetId/modules'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is List) {
        return items.cast<Map<String, dynamic>>();
      }
      return const [];
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> getCabinetModuleMeta({
    required String cabinetId,
    required String moduleId,
    required String slug,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.get(
        _uri('/cabinets/$cabinetId/modules/$moduleId/meta/documents/$slug'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<List<Map<String, dynamic>>> listModuleDataRows({
    required String cabinetId,
    required String moduleId,
    required String tableSlug,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.get(
        _uri('/cabinets/$cabinetId/modules/$moduleId/data/$tableSlug'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is List) {
        return items.cast<Map<String, dynamic>>();
      }
      return const [];
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> createModuleDataRow({
    required String cabinetId,
    required String moduleId,
    required String tableSlug,
    required Map<String, dynamic> body,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.post(
        _uri('/cabinets/$cabinetId/modules/$moduleId/data/$tableSlug'),
        body: jsonEncode({'body': body}),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> updateModuleDataRow({
    required String cabinetId,
    required String moduleId,
    required String tableSlug,
    required String rowId,
    required Map<String, dynamic> body,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.patch(
        _uri('/cabinets/$cabinetId/modules/$moduleId/data/$tableSlug/$rowId'),
        body: jsonEncode({'body': body}),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> deleteModuleDataRow({
    required String cabinetId,
    required String moduleId,
    required String tableSlug,
    required String rowId,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.delete(
        _uri('/cabinets/$cabinetId/modules/$moduleId/data/$tableSlug/$rowId'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      if (res.body.isEmpty) return <String, dynamic>{'deleted': true};
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  /// Project leaf module instances (Management/Data hubs).
  Future<List<Map<String, dynamic>>> listProjectRuntimeModules(String projectId) async {
    final res = await AuthHttp.get(
      _uri('/projects/$projectId/runtime-modules'),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> getProjectRuntimeModuleMeta({
    required String projectId,
    required String moduleId,
    required String slug,
  }) async {
    final res = await AuthHttp.get(
      _uri('/projects/$projectId/runtime-modules/$moduleId/meta/documents/$slug'),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listProjectRuntimeModuleDataRows({
    required String projectId,
    required String moduleId,
    required String tableSlug,
  }) async {
    final res = await AuthHttp.get(
      _uri('/projects/$projectId/runtime-modules/$moduleId/data/$tableSlug'),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final items = body['items'];
    if (items is List) {
      return items.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> createProjectRuntimeModuleDataRow({
    required String projectId,
    required String moduleId,
    required String tableSlug,
    required Map<String, dynamic> body,
  }) async {
    final res = await AuthHttp.post(
      _uri('/projects/$projectId/runtime-modules/$moduleId/data/$tableSlug'),
      body: jsonEncode({'body': body}),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> updateProjectRuntimeModuleDataRow({
    required String projectId,
    required String moduleId,
    required String tableSlug,
    required String rowId,
    required Map<String, dynamic> body,
  }) async {
    final res = await AuthHttp.patch(
      _uri('/projects/$projectId/runtime-modules/$moduleId/data/$tableSlug/$rowId'),
      body: jsonEncode({'body': body}),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> deleteProjectRuntimeModuleDataRow({
    required String projectId,
    required String moduleId,
    required String tableSlug,
    required String rowId,
  }) async {
    final res = await AuthHttp.delete(
      _uri('/projects/$projectId/runtime-modules/$moduleId/data/$tableSlug/$rowId'),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
    if (res.body.isEmpty) return <String, dynamic>{'deleted': true};
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> invokeModuleAction({
    required String cabinetId,
    required String moduleId,
    required String actionId,
    String? rowId,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.post(
        _uri('/cabinets/$cabinetId/modules/$moduleId/actions/$actionId/invoke'),
        body: jsonEncode({if (rowId != null) 'row_id': rowId}),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> uploadCabinetModuleSecret({
    required String cabinetId,
    required String moduleId,
    required String secret,
    String? label,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.post(
        _uri('/cabinets/$cabinetId/modules/$moduleId/secrets/upload'),
        body: jsonEncode({
          'secret': secret,
          if (label != null && label.isNotEmpty) 'label': label,
        }),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> uploadCabinetContent({
    required String cabinetId,
    required String filename,
    required List<int> bytes,
    String? mime,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final req = http.MultipartRequest(
        'POST',
        _uri('/cabinets/$cabinetId/content/upload'),
      );
      final h = await AuthHttp.headers(_workHeaders);
      h.remove('Content-Type');
      req.headers.addAll(h);
      req.files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
      final streamed = await req.send();
      final res = await http.Response.fromStream(streamed);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<List<Map<String, dynamic>>> listProjects(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.get(_uri('/cabinets/$cabinetId/projects'), extraHeaders: _workHeaders);
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is List) {
        return items.cast<Map<String, dynamic>>();
      }
      return const [];
    } finally {
      this.cabinetId = prev;
    }
  }

  /// Projects with an explicit module↔project (MP) bind for [moduleId].
  Future<List<Map<String, dynamic>>> listModuleBoundProjects({
    required String cabinetId,
    required String moduleId,
  }) async {
    if (moduleId.isEmpty) return const [];
    try {
      final res = await AuthHttp.get(
        _uri('/cabinets/$cabinetId/modules/$moduleId/bound-projects'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is! List) return const [];
      return [
        for (final raw in items)
          if (raw is Map)
            {
              'id': raw['project_id']?.toString() ?? '',
              'name': raw['name']?.toString() ?? '',
            },
      ].where((e) => (e['id'] as String).isNotEmpty).toList();
    } catch (_) {
      // Fallback: filter cabinet projects by enabled module list.
      final projects = await listProjects(cabinetId);
      if (projects.isEmpty) return const [];
      final checks = await Future.wait(
        projects.map((p) async {
          final id = p['id'] as String?;
          if (id == null || id.isEmpty) return null;
          try {
            final mods = await listProjectModuleIds(id);
            return mods.contains(moduleId) ? p : null;
          } catch (_) {
            return null;
          }
        }),
      );
      return checks.whereType<Map<String, dynamic>>().toList();
    }
  }

  Future<Map<String, dynamic>> createProject({
    required String cabinetId,
    required String name,
    String? agentProvider,
  }) async {
    final prevCab = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.post(_uri('/cabinets/$cabinetId/projects'), body: jsonEncode({
          'name': name,
          if (agentProvider != null && agentProvider.isNotEmpty) 'agent_provider': agentProvider,
        }), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prevCab;
    }
  }

  Future<Map<String, dynamic>> getProject(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> patchProject({
    required String projectId,
    String? name,
    String? about,
    int? budgetTokens,
    bool updateBudgetTokens = false,
    String? agentProvider,
    bool clearAgentProvider = false,
    String? resolvedAiKeyId,
    bool clearResolvedAiKeyId = false,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final body = <String, dynamic>{};
      if (name != null) body['name'] = name;
      if (about != null) body['about'] = about;
      if (updateBudgetTokens) body['budget_tokens'] = budgetTokens;
      if (clearAgentProvider) {
        body['agent_provider'] = null;
      } else if (agentProvider != null) {
        body['agent_provider'] = agentProvider;
      }
      if (clearResolvedAiKeyId) {
        body['resolved_ai_key_id'] = null;
      } else if (resolvedAiKeyId != null) {
        body['resolved_ai_key_id'] = resolvedAiKeyId;
      }
      final res = await AuthHttp.patch(_uri('/projects/$projectId'), body: jsonEncode(body), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<void> deleteProject(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.delete(_uri('/projects/$projectId'), extraHeaders: _workHeaders);
      _throwIfError(res);
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<List<Map<String, dynamic>>> listProjectAiKeys(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/ai-keys/available'), extraHeaders: _workHeaders);
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is List) return items.cast<Map<String, dynamic>>();
      return const [];
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> getCabinetMetrics(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.get(_uri('/cabinets/$cabinetId/metrics'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  /// Keep Redis presence alive while the cabinet shell is open.
  Future<void> cabinetPresenceHeartbeat(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.post(
        _uri('/cabinets/$cabinetId/presence/heartbeat'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> getProjectMetrics(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/metrics'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> getProjectContainer(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/container'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> getProjectContainerMetrics(
    String projectId, {
    String window = '1h',
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(
        _uri('/projects/$projectId/container/metrics?window=${Uri.encodeComponent(window)}'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> reloadProject(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/reload'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> listWorkspaceEntries({
    required String projectId,
    String path = '',
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(
        _uri('/projects/$projectId/container/workspace/entries').replace(
          queryParameters: {'path': path},
        ),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> previewWorkspaceFile({
    required String projectId,
    required String path,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(
        _uri('/projects/$projectId/container/workspace/preview').replace(
          queryParameters: {'path': path},
        ),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Uint8List> downloadWorkspaceFile({
    required String projectId,
    required String path,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(
        _uri('/projects/$projectId/container/workspace/content').replace(
          queryParameters: {'path': path},
        ),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return res.bodyBytes;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<List<String>> listProjectModuleIds(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/modules'), extraHeaders: _workHeaders);
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['module_ids'];
      if (items is List) return items.cast<String>();
      return const [];
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<List<Map<String, dynamic>>> listProjectModules(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/modules'), extraHeaders: _workHeaders);
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is List) return items.cast<Map<String, dynamic>>();
      return const [];
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> getProjectModule(String projectId, String moduleId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(
        _uri('/projects/$projectId/modules/$moduleId'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> patchProjectModuleProfile({
    required String projectId,
    required String moduleId,
    required String profileId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.patch(
        _uri('/projects/$projectId/modules/$moduleId/profile'),
        body: jsonEncode({'profile_id': profileId}),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> patchProjectModules(String projectId, {required List<String> moduleIds}) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.patch(
        _uri('/projects/$projectId/modules'),
        body: jsonEncode({'module_ids': moduleIds}),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      if (res.body.isEmpty) return <String, dynamic>{'module_ids': moduleIds};
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> bindProjectModule(
    String projectId, {
    required String moduleId,
    required String bindKind,
    bool? childMayEdit,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final payload = <String, dynamic>{'bind_kind': bindKind};
      if (childMayEdit != null) payload['child_may_edit'] = childMayEdit;
      final res = await AuthHttp.post(
        _uri('/projects/$projectId/modules/$moduleId/bind'),
        body: jsonEncode(payload),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> revokeProjectModule(
    String projectId, {
    required String moduleId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.delete(
        _uri('/projects/$projectId/modules/$moduleId'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      if (res.body.isEmpty) {
        return <String, dynamic>{
          'module_id': moduleId,
          'project_id': projectId,
          'status': 'revoked',
        };
      }
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<void> setMyPassword(String password) async {
    final res = await AuthHttp.put(
      _uri('/me/password'),
      body: jsonEncode({'password': password}),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
  }

  Future<void> patchMyContactEmail(String contactEmail) async {
    final res = await AuthHttp.patch(
      _uri('/me/contact-email'),
      body: jsonEncode({'contact_email': contactEmail.isEmpty ? null : contactEmail}),
      extraHeaders: _workHeaders,
    );
    _throwIfError(res);
  }

  Future<Map<String, dynamic>> launchProject(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/launch'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> syncProject(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/sync'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> resetProjectAgent(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/agent/reset'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> rematerializeProject(String projectId) async {
    return syncProject(projectId);
  }

  Future<Map<String, dynamic>> pauseProject(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/pause'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> resumeProject(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/resume'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> listProjectModelsLive(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/models/live'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> projectChat({
    required String projectId,
    required String text,
    String? sessionId,
    List<String> attachmentRefs = const [],
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/chat'), body: jsonEncode({
          'text': text,
          if (sessionId != null) 'session_id': sessionId,
          if (attachmentRefs.isNotEmpty) 'attachment_refs': attachmentRefs,
        }), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<List<Map<String, dynamic>>> listProjectAttachments(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/attachments'), extraHeaders: _workHeaders);
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is List) {
        return items.cast<Map<String, dynamic>>();
      }
      return const [];
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> uploadProjectAttachment({
    required String projectId,
    required String filename,
    required List<int> bytes,
    String? contentType,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/attachments'), body: jsonEncode({
          'filename': filename,
          'content_base64': base64Encode(bytes),
          if (contentType != null && contentType.isNotEmpty) 'content_type': contentType,
        }), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<void> deleteProjectAttachment({
    required String projectId,
    required String attachmentId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.delete(_uri('/projects/$projectId/attachments/$attachmentId'), extraHeaders: _workHeaders);
      _throwIfError(res);
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Uint8List> downloadProjectAttachmentBytes({
    required String projectId,
    required String attachmentId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/attachments/$attachmentId/content'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return res.bodyBytes;
    } finally {
      this.projectId = prevProj;
    }
  }

  /// SSE chat turn — abort via [ProjectChatStreamHandle.abort] (L05).
  ProjectChatStreamHandle projectChatStream({
    required String projectId,
    required String text,
    String? sessionId,
    String? model,
    List<String> attachmentRefs = const [],
  }) {
    final prevProj = this.projectId;
    this.projectId = projectId;
    final client = http.Client();
    final controller = StreamController<Map<String, dynamic>>();

    Future<void> pump() async {
      try {
        final request = http.Request('POST', _uri('/projects/$projectId/chat/stream'))
          ..headers.addAll(await AuthHttp.headers(_workHeaders))
          ..body = jsonEncode({
            'text': text,
            if (sessionId != null) 'session_id': sessionId,
            if (model != null && model.isNotEmpty) 'model': model,
            if (attachmentRefs.isNotEmpty) 'attachment_refs': attachmentRefs,
          });
        final response = await client.send(request);
        if (response.statusCode >= 400) {
          final body = await response.stream.bytesToString();
          throw ProdavanApiException(response.statusCode, body);
        }
        var buffer = '';
        await for (final chunk in response.stream.transform(utf8.decoder)) {
          if (controller.isClosed) break;
          buffer += chunk;
          while (true) {
            final sep = buffer.indexOf('\n\n');
            if (sep < 0) break;
            final block = buffer.substring(0, sep);
            buffer = buffer.substring(sep + 2);
            for (final line in block.split('\n')) {
              if (!line.startsWith('data: ')) continue;
              final payload = jsonDecode(line.substring(6));
              if (payload is Map<String, dynamic> && !controller.isClosed) {
                controller.add(payload);
              }
            }
          }
        }
      } catch (e, st) {
        if (!controller.isClosed) {
          controller.addError(e, st);
        }
      } finally {
        client.close();
        this.projectId = prevProj;
        if (!controller.isClosed) {
          await controller.close();
        }
      }
    }

    pump();

    return ProjectChatStreamHandle(
      stream: controller.stream,
      abort: () {
        client.close();
        if (!controller.isClosed) {
          controller.close();
        }
      },
    );
  }

  Future<Map<String, dynamic>> getProjectSelection(String cabinetId) async {
    final prevCab = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.get(
        _uri('/cabinets/$cabinetId/me/selection'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prevCab;
    }
  }

  Future<Map<String, dynamic>> putProjectSelection({
    required String cabinetId,
    String? projectId,
  }) async {
    final prevCab = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.put(
        _uri('/cabinets/$cabinetId/me/selection'),
        body: jsonEncode({'project_id': projectId}),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prevCab;
    }
  }

  Future<Map<String, dynamic>> getChatsSidebar(String cabinetId) async {
    final prevCab = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await AuthHttp.get(
        _uri('/cabinets/$cabinetId/chats/sidebar'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prevCab;
    }
  }

  Future<Map<String, dynamic>> createAgentSession({
    required String projectId,
    String? model,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(
        _uri('/projects/$projectId/agent/sessions'),
        body: jsonEncode({
          if (model != null && model.isNotEmpty) 'model': model,
        }),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> patchAgentSession({
    required String projectId,
    required String sessionId,
    String? title,
    bool? pin,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final body = <String, dynamic>{};
      if (title != null) body['title'] = title;
      if (pin != null) body['pin'] = pin;
      final res = await AuthHttp.patch(
        _uri('/projects/$projectId/agent/sessions/$sessionId'),
        body: jsonEncode(body),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<void> deleteAgentSession({
    required String projectId,
    required String sessionId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.delete(
        _uri('/projects/$projectId/agent/sessions/$sessionId'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> projectChatTranscript({
    required String projectId,
    required String sessionId,
    int limit = 100,
    int? beforeSeq,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final params = <String, String>{'session_id': sessionId};
      if (limit != 100) params['limit'] = '$limit';
      if (beforeSeq != null) params['before_seq'] = '$beforeSeq';
      final query =
          '?${params.entries.map((e) => '${e.key}=${Uri.encodeComponent(e.value)}').join('&')}';
      final res = await AuthHttp.get(_uri('/projects/$projectId/chat/transcript$query'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<void> cancelAgentSession({
    required String projectId,
    required String sessionId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/agent/sessions/$sessionId/cancel'), extraHeaders: _workHeaders);
      _throwIfError(res);
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<List<Map<String, dynamic>>> listPendingApprovals({
    required String projectId,
    required String sessionId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/agent/sessions/$sessionId/pending-approvals'), extraHeaders: _workHeaders);
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is List) {
        return items.cast<Map<String, dynamic>>();
      }
      return const [];
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> resolveToolApproval({
    required String projectId,
    required String sessionId,
    required String approvalId,
    required String decision,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/agent/sessions/$sessionId/tool-approvals'), body: jsonEncode({'id': approvalId, 'decision': decision}), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<List<Map<String, dynamic>>> listAgentSessions({
    required String projectId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(_uri('/projects/$projectId/agent/sessions'), extraHeaders: _workHeaders);
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final items = body['items'];
      if (items is List) {
        return items.cast<Map<String, dynamic>>();
      }
      return const [];
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> getSidechainTranscript({
    required String projectId,
    required String sessionId,
    required String toolUseId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.get(
        _uri('/projects/$projectId/agent/sessions/$sessionId/sidechains/$toolUseId/transcript'),
        extraHeaders: _workHeaders,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  void _throwIfError(http.Response res) {
    if (res.statusCode >= 400) {
      throw ProdavanApiException(res.statusCode, res.body);
    }
  }
}

/// Abortable SSE chat stream (L05) — close HTTP client to stop mid-flight.
class ProjectChatStreamHandle {
  ProjectChatStreamHandle({required this.stream, required this.abort});

  final Stream<Map<String, dynamic>> stream;
  final void Function() abort;
}

class ProdavanApiException implements Exception {
  ProdavanApiException(this.statusCode, this.body);
  final int statusCode;
  final String body;

  @override
  String toString() {
    final trimmed = body.trim();
    final lower = trimmed.toLowerCase();
    if (trimmed.isEmpty ||
        lower.contains('<html') ||
        lower.contains('<!doctype') ||
        lower.contains('nginx/')) {
      return 'ProdavanApiException($statusCode)';
    }
    final short =
        trimmed.length > 160 ? '${trimmed.substring(0, 160)}…' : trimmed;
    return 'ProdavanApiException($statusCode): $short';
  }
}
