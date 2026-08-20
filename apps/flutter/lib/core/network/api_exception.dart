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
    final code = body['code']?.toString() ?? 'HTTP_ERROR';
    final message = _extractMessage(body);
    return ApiException(
      statusCode: statusCode,
      code: code,
      message: message,
    );
  }

  static String _extractMessage(Map<String, dynamic> body) {
    final message = body['message'];
    if (message is String && message.trim().isNotEmpty) {
      return message.trim();
    }
    final detail = body['detail'];
    if (detail is String && detail.trim().isNotEmpty) {
      return detail.trim();
    }
    if (detail is Map && detail['message'] != null) {
      return detail['message'].toString();
    }
    // Never surface raw list dumps like [{...}] to UI.
    if (detail is List) {
      return 'Проверьте введённые данные';
    }
    return 'Request failed';
  }

  @override
  String toString() => '$code: $message';
}
