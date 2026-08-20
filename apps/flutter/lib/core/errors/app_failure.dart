/// Normalized app-facing failure (never raw JSON / list dumps).
class AppFailure {
  const AppFailure({
    required this.message,
    this.code = 'ERROR',
    this.statusCode,
  });

  final String message;
  final String code;
  final int? statusCode;
}
