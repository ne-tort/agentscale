import 'package:prodavan/core/network/api_client.dart';

class AdminRemoteDataSource {
  AdminRemoteDataSource(this._client);

  final ApiClient _client;

  Future<List<dynamic>> listUsers() async {
    final data = await _client.getList('/admin/users');
    return data;
  }

  Future<Map<String, dynamic>> createUser(Map<String, dynamic> body) =>
      _client.post('/admin/users', body: body);

  Future<Map<String, dynamic>> getUser(String id) => _client.get('/admin/users/$id');

  Future<Map<String, dynamic>> updateUser(String id, Map<String, dynamic> body) =>
      _client.patch('/admin/users/$id', body: body);

  Future<Map<String, dynamic>> deleteUser(String id) =>
      _client.delete('/admin/users/$id');

  Future<Map<String, dynamic>> stats() => _client.get('/admin/stats');
}
