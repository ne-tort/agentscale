import 'dart:convert';

import 'package:prodavan/core/api/prodavan_api.dart';

class WorkspaceDownloadLimit {
  const WorkspaceDownloadLimit({required this.size, required this.maxBytes});

  final int size;
  final int maxBytes;
}

WorkspaceDownloadLimit? workspaceDownloadLimit(Object error) {
  if (error is! ProdavanApiException || error.statusCode != 413) return null;
  try {
    final body = jsonDecode(error.body);
    if (body is! Map || body['code'] != 'PAYLOAD_TOO_LARGE') return null;
    // FastAPI problem responses flatten AppError.extra at the top level.
    // Accept a nested shape too for compatible gateways/test doubles.
    final extra = body['extra'];
    final metadata = extra is Map ? extra : body;
    final size = metadata['size'];
    final maxBytes = metadata['max_bytes'];
    if (size is! int || maxBytes is! int || size <= maxBytes || maxBytes <= 0) {
      return null;
    }
    return WorkspaceDownloadLimit(size: size, maxBytes: maxBytes);
  } on FormatException {
    return null;
  }
}
