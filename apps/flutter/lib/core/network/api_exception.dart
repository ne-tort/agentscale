/// HTTP failures from Prodavan API (RFC 7807 problem+json).
class ApiException implements Exception {
  ApiException({
    required this.statusCode,
    required this.code,
    required this.message,
  });

  final int statusCode;
  final String code;
  final String message;

  factory ApiException.fromJson(int statusCode, Map<String, dynamic> body) {
    return ApiException(
      statusCode: statusCode,
      code: body['code']?.toString() ?? 'HTTP_ERROR',
      message: body['detail']?.toString() ?? body['message']?.toString() ?? 'Request failed',
    );
  }

  @override
  String toString() => '$code: $message';
}
