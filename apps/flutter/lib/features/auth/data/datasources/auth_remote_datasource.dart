import 'package:prodavan/core/network/api_client.dart';

class AuthRemoteDataSource {
  AuthRemoteDataSource(this._authApi, this._client);

  final AuthApi _authApi;
  final ApiClient _client;

  Future<Map<String, dynamic>> login(Map<String, dynamic> body) =>
      _authApi.login(body);

  Future<Map<String, dynamic>> refresh(String refreshToken) =>
      _authApi.refresh(refreshToken);

  Future<Map<String, dynamic>> me() => _authApi.me();

  Future<Map<String, dynamic>> updateProfile(Map<String, dynamic> body) =>
      _client.patch('/me/profile', body: body);

  Future<void> changePassword(Map<String, dynamic> body) async {
    await _client.post('/me/password', body: body);
  }
}
