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
    String? agentProvider,
    bool clearAgentProvider = false,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final body = <String, dynamic>{};
      if (name != null) body['name'] = name;
      if (clearAgentProvider) {
        body['agent_provider'] = null;
      } else if (agentProvider != null) {
        body['agent_provider'] = agentProvider;
      }
      final res = await AuthHttp.patch(_uri('/projects/$projectId'), body: jsonEncode(body), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  Future<Map<String, dynamic>> rematerializeProject(String projectId) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final res = await AuthHttp.post(_uri('/projects/$projectId/rematerialize'), extraHeaders: _workHeaders);
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
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

  Future<Map<String, dynamic>> projectChatTranscript({
    required String projectId,
    String? sessionId,
  }) async {
    final prevProj = this.projectId;
    this.projectId = projectId;
    try {
      final query = sessionId == null ? '' : '?session_id=$sessionId';
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
