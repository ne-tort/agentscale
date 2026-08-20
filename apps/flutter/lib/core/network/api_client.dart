import 'dart:convert';

import 'package:http/http.dart' as http;

import 'package:prodavan/core/config/api_config.dart';
import 'package:prodavan/core/network/api_exception.dart';

typedef TokenProvider = String? Function();
typedef CabinetIdProvider = String? Function();

/// Thin HTTP client: auth header, optional X-Cabinet-Id, problem+json errors.
class ApiClient {
  ApiClient({
    ApiConfig config = const ApiConfig(),
    http.Client? httpClient,
    TokenProvider? tokenProvider,
    CabinetIdProvider? cabinetIdProvider,
  })  : _config = config,
        _http = httpClient ?? http.Client(),
        _tokenProvider = tokenProvider,
        _cabinetIdProvider = cabinetIdProvider;

  final ApiConfig _config;
  final http.Client _http;
  final TokenProvider? _tokenProvider;
  final CabinetIdProvider? _cabinetIdProvider;

  Uri _uri(String path, [Map<String, String>? query]) {
    final base = '${_config.v1Prefix}$path';
    return query == null ? Uri.parse(base) : Uri.parse(base).replace(queryParameters: query);
  }

  Map<String, String> _headers({Map<String, String>? extra, String? ifMatch}) {
    final headers = <String, String>{
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    };
    final token = _tokenProvider?.call();
    if (token != null && token.isNotEmpty) {
      headers['Authorization'] = 'Bearer $token';
    }
    final cabinetId = _cabinetIdProvider?.call();
    if (cabinetId != null && cabinetId.isNotEmpty) {
      headers['X-Cabinet-Id'] = cabinetId;
    }
    if (ifMatch != null) {
      headers['If-Match'] = ifMatch;
    }
    if (extra != null) {
      headers.addAll(extra);
    }
    return headers;
  }

  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) async {
    final response = await _http.get(_uri(path, query), headers: _headers());
    return _decode(response);
  }

  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
  }) async {
    final response = await _http.post(
      _uri(path),
      headers: _headers(),
      body: body == null ? null : jsonEncode(body),
    );
    return _decode(response);
  }

  Future<Map<String, dynamic>> put(
    String path, {
    Map<String, dynamic>? body,
    String? ifMatch,
  }) async {
    final response = await _http.put(
      _uri(path),
      headers: _headers(ifMatch: ifMatch),
      body: body == null ? null : jsonEncode(body),
    );
    return _decode(response);
  }

  Map<String, dynamic> _decode(http.Response response) {
    Map<String, dynamic> body = {};
    if (response.body.isNotEmpty) {
      final decoded = jsonDecode(response.body);
      if (decoded is Map<String, dynamic>) {
        body = decoded;
      }
    }
    if (response.statusCode >= 400) {
      throw ApiException.fromJson(response.statusCode, body);
    }
    return body;
  }

  Future<Map<String, dynamic>> postMultipart(
    String path, {
    required String fileField,
    required String filename,
    required List<int> bytes,
    Map<String, String> fields = const {},
  }) async {
    final request = http.MultipartRequest('POST', _uri(path));
    final headers = _headers();
    headers.remove('Content-Type');
    request.headers.addAll(headers);
    request.fields.addAll(fields);
    request.files.add(http.MultipartFile.fromBytes(fileField, bytes, filename: filename));
    final streamed = await _http.send(request);
    final response = await http.Response.fromStream(streamed);
    return _decode(response);
  }

  void dispose() => _http.close();
}

/// Auth endpoints without cabinet header.
class AuthApi {
  AuthApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> register(Map<String, dynamic> body) =>
      _client.post('/auth/register', body: body);

  Future<Map<String, dynamic>> login(Map<String, dynamic> body) =>
      _client.post('/auth/login', body: body);

  Future<Map<String, dynamic>> me() => _client.get('/me');
}

/// Cabinet-scoped endpoints (require X-Cabinet-Id via client provider).
class CabinetsApi {
  CabinetsApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> list({String status = 'active'}) =>
      _client.get('/cabinets', query: {'status': status});

  Future<Map<String, dynamic>> switchCabinet(String cabinetId) =>
      _client.post('/cabinets/$cabinetId/switch');

  Future<Map<String, dynamic>> create({
    required String slug,
    required String displayName,
    required String profileId,
  }) =>
      _client.post('/cabinets', body: {
        'slug': slug,
        'display_name': displayName,
        'profile_id': profileId,
      });

  Future<Map<String, dynamic>> manifest(String cabinetId) =>
      _client.get('/cabinets/$cabinetId/manifest');

  Future<Map<String, dynamic>> capabilities(String cabinetId) =>
      _client.get('/cabinets/$cabinetId/capabilities');
}

class ProjectsApi {
  ProjectsApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> list({String status = 'active'}) =>
      _client.get('/projects', query: {'status': status});

  Future<Map<String, dynamic>> create({
    required String slug,
    required String displayName,
  }) =>
      _client.post('/projects', body: {
        'slug': slug,
        'display_name': displayName,
      });

  Future<Map<String, dynamic>> open(String projectId) =>
      _client.post('/projects/$projectId/open');

  Future<Map<String, dynamic>> stats(String projectId) =>
      _client.get('/projects/$projectId/stats');
}

class CatalogsApi {
  CatalogsApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> upload({
    required String cabinetId,
    required String filename,
    required List<int> bytes,
    required String slug,
    required String displayName,
    bool trustedSeller = true,
  }) =>
      _client.postMultipart(
        '/cabinets/$cabinetId/catalogs/upload',
        fileField: 'file',
        filename: filename,
        bytes: bytes,
        fields: {
          'slug': slug,
          'display_name': displayName,
          'trusted_seller': trustedSeller.toString(),
        },
      );
}

class SpecsApi {
  SpecsApi(this._client);

  final ApiClient _client;

  static const pipelineToReview = ['classify', 'search', 'rank', 'variants', 'review'];

  Future<Map<String, dynamic>> uploadInbox({
    required String projectId,
    required String filename,
    required List<int> bytes,
    bool autoRun = true,
  }) =>
      _client.postMultipart(
        '/projects/$projectId/inbox/upload',
        fileField: 'file',
        filename: filename,
        bytes: bytes,
        fields: {'auto_run': autoRun.toString()},
      );

  Future<Map<String, dynamic>> advance({
    required String projectId,
    required String runId,
    required String targetPhase,
  }) =>
      _client.post('/projects/$projectId/runs/$runId/advance', body: {
        'target_phase': targetPhase,
      });

  Future<Map<String, dynamic>> exportKp({
    required String projectId,
    required String runId,
  }) =>
      _client.post('/projects/$projectId/export/kp', body: {
        'run_id': runId,
        'include_alternatives': true,
      });
}
