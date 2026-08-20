import 'package:prodavan/features/auth/data/datasources/auth_remote_datasource.dart';
import 'package:prodavan/features/auth/domain/entities/user_session.dart';
import 'package:prodavan/features/auth/domain/repositories/auth_repository.dart';

class AuthRepositoryImpl implements AuthRepository {
  AuthRepositoryImpl(this._remote);

  final AuthRemoteDataSource _remote;

  Map<String, dynamic>? _lastAuth;

  Map<String, dynamic>? get lastAuthResponse => _lastAuth;

  @override
  Future<UserSession> login({required String loginId, required String password}) async {
    final data = await _remote.login({
      'login_id': loginId,
      'password': password,
    });
    _lastAuth = data;
    return UserSession.fromJson(data['user'] as Map<String, dynamic>);
  }

  @override
  Future<UserSession> me() async {
    final data = await _remote.me();
    return UserSession.fromJson(data['user'] as Map<String, dynamic>);
  }

  @override
  Future<UserSession> updateProfile({
    String? contactPerson,
    String? phone,
    String? email,
  }) async {
    final body = <String, dynamic>{};
    if (contactPerson != null) {
      body['contact_person'] = contactPerson.trim().isEmpty ? null : contactPerson.trim();
    }
    if (phone != null) {
      body['phone'] = phone.trim().isEmpty ? null : phone.trim();
    }
    if (email != null) {
      // Empty string clears email on API (coerced to null); omit if we don't want to touch.
      body['email'] = email.trim().isEmpty ? '' : email.trim();
    }
    final data = await _remote.updateProfile(body);
    return UserSession.fromJson(data);
  }

  @override
  Future<void> changePassword({
    required String currentPassword,
    required String newPassword,
  }) {
    return _remote.changePassword({
      'current_password': currentPassword,
      'new_password': newPassword,
    });
  }

  @override
  Future<bool> refreshSession(String refreshToken) async {
    try {
      final data = await _remote.refresh(refreshToken);
      _lastAuth = data;
      return true;
    } catch (_) {
      return false;
    }
  }
}
