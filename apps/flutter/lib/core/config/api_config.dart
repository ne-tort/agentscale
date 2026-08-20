import 'package:prodavan/core/config/env.dart';

class ApiConfig {
  const ApiConfig();

  String get baseUrl => Env.apiBase;

  String get v1Prefix => '$baseUrl/api/v1';
}
