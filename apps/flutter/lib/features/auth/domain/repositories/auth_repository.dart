import 'package:prodavan/features/auth/domain/entities/user_session.dart';

abstract class AuthRepository {
  Future<UserSession> login({required String loginId, required String password});

  Future<UserSession> me();

  Future<UserSession> updateProfile({
    String? contactPerson,
    String? phone,
    String? email,
  });

  Future<void> changePassword({
    required String currentPassword,
    required String newPassword,
  });

  Future<bool> refreshSession(String refreshToken);
}
