import 'dart:convert';

import 'package:http/http.dart' as http;

/// Public auth discovery from GET /auth/config (L01).
class AuthConfigClient {
  AuthConfigClient({required this.baseUrl});

  final String baseUrl;

  String get _root => baseUrl.replaceAll(RegExp(r'/+$'), '');

  Future<Map<String, dynamic>> fetch() async {
    final uri = Uri.parse('$_root/auth/config');
    final res = await http.get(uri);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw Exception('auth/config ${res.statusCode}: ${res.body}');
    }
    return jsonDecode(res.body) as Map<String, dynamic>;
  }
}
