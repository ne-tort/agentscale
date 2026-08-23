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
        }),
      );
      _throwIfError(res);
      return jsonDecode(res.body) as Map<String, dynamic>;
    } finally {
      this.projectId = prevProj;
    }
  }

  /// SSE chat turn — yields platform/agent events (L05 streaming).
  Stream<Map<String, dynamic>> projectChatStream({
    required String projectId,
    required String text,
    String? sessionId,
  }) async* {
    final prevProj = this.projectId;
    this.projectId = projectId;
    final client = http.Client();
    try {
      final request = http.Request('POST', _uri('/projects/$projectId/chat/stream'))
        ..headers.addAll(_headers)
        ..body = jsonEncode({
          'text': text,
          if (sessionId != null) 'session_id': sessionId,
        });
      final response = await client.send(request);
      if (response.statusCode >= 400) {
        final body = await response.stream.bytesToString();
        throw ProdavanApiException(response.statusCode, body);
      }
      var buffer = '';
      await for (final chunk in response.stream.transform(utf8.decoder)) {
        buffer += chunk;
        while (true) {
          final sep = buffer.indexOf('\n\n');
          if (sep < 0) break;
          final block = buffer.substring(0, sep);
          buffer = buffer.substring(sep + 2);
          for (final line in block.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            final payload = jsonDecode(line.substring(6));
            if (payload is Map<String, dynamic>) {
              yield payload;
            }
          }
        }
      }
    } finally {
      client.close();
      this.projectId = prevProj;
    }
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

  void _throwIfError(http.Response res) {
    if (res.statusCode >= 400) {
      throw ProdavanApiException(res.statusCode, res.body);
    }
  }
}

class ProdavanApiException implements Exception {
  ProdavanApiException(this.statusCode, this.body);
  final int statusCode;
  final String body;

  @override
  String toString() => 'ProdavanApiException($statusCode): $body';
}
