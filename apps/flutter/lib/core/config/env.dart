/// Runtime environment flags (compile-time defines).
class Env {
  const Env._();

  static const apiBase = String.fromEnvironment(
    'API_BASE',
    defaultValue: 'http://localhost:8000',
  );
}
