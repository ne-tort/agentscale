import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

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

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $bearerToken',
        'Content-Type': 'application/json',
        if (cabinetId != null) 'X-Cabinet-Id': cabinetId!,
        if (projectId != null) 'X-Project-Id': projectId!,
      };

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, dynamic>> me() async {
    final res = await http.get(_uri('/me'), headers: _headers);
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listCabinets() async {
    final res = await http.get(_uri('/cabinets'), headers: _headers);
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) {
      return body.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<Map<String, dynamic>> createCabinet({
    required String name,
    required String companyId,
  }) async {
    final res = await http.post(
      _uri('/cabinets'),
      headers: _headers,
      body: jsonEncode({'name': name, 'company_id': companyId}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> listMetaTabs(String cabinetId) async {
    final res = await http.get(_uri('/cabinets/$cabinetId/meta/tabs'), headers: _headers);
    _throwIfError(res);
    final body = jsonDecode(res.body);
    if (body is List) {
      return body.cast<Map<String, dynamic>>();
    }
    return const [];
  }

  Future<List<Map<String, dynamic>>> listMetaTables(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await http.get(_uri('/cabinets/$cabinetId/meta/tables'), headers: _headers);
      _throwIfError(res);
      final body = jsonDecode(res.body);
      if (body is List) {
        return body.cast<Map<String, dynamic>>();
      }
      return const [];
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<Map<String, dynamic>> queryCabinetRows({
    required String cabinetId,
    required String tableSlug,
    int limit = 50,
    int offset = 0,
  }) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await http.get(
        _uri('/cabinets/$cabinetId/data/$tableSlug/rows?limit=$limit&offset=$offset'),
        headers: _headers,
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<List<Map<String, dynamic>>> listCabinetMcpTools(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await http.get(_uri('/cabinets/$cabinetId/mcp/tools'), headers: _headers);
      _throwIfError(res);
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final tools = body['tools'];
      if (tools is List) {
        return tools.cast<Map<String, dynamic>>();
      }
      return const [];
    } finally {
      this.cabinetId = prev;
    }
  }

  Future<List<Map<String, dynamic>>> listProjects(String cabinetId) async {
    final prev = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await http.get(_uri('/cabinets/$cabinetId/projects'), headers: _headers);
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
  }) async {
    final prevCab = this.cabinetId;
    this.cabinetId = cabinetId;
    try {
      final res = await http.post(
        _uri('/cabinets/$cabinetId/projects'),
        headers: _headers,
        body: jsonEncode({'name': name}),
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.cabinetId = prevCab;
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
      final res = await http.post(
        _uri('/projects/$projectId/chat'),
        headers: _headers,
        body: jsonEncode({
          'text': text,
          if (sessionId != null) 'session_id': sessionId,
          if (attachmentRefs.isNotEmpty) 'attachment_refs': attachmentRefs,
        }),
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
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
      final res = await http.post(
        _uri('/projects/$projectId/attachments'),
        headers: _headers,
        body: jsonEncode({
          'filename': filename,
          'content_base64': base64Encode(bytes),
          if (contentType != null && contentType.isNotEmpty) 'content_type': contentType,
        }),
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
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
          ..headers.addAll(_headers)
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
      final res = await http.get(
        _uri('/projects/$projectId/chat/transcript$query'),
        headers: _headers,
      );
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
      final res = await http.post(
        _uri('/projects/$projectId/agent/sessions/$sessionId/cancel'),
        headers: _headers,
      );
      _throwIfError(res);
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
  String toString() => 'ProdavanApiException($statusCode): $body';
}
