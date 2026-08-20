// Auth repository port — session handled by [AppState].

abstract class AuthRepository {
  Future<bool> isAuthenticated();
}
