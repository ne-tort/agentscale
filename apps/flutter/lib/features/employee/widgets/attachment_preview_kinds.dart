import 'package:flutter/material.dart';

bool attachmentIsImageContentType(String? contentType) {
  return contentType != null && contentType.startsWith('image/');
}

bool attachmentIsPdfContentType(String? contentType) {
  return contentType == 'application/pdf';
}

bool attachmentIsTextContentType(String? contentType) {
  if (contentType == null) return false;
  if (contentType.startsWith('text/')) return true;
  return contentType == 'application/json' ||
      contentType == 'application/xml' ||
      contentType == 'application/x-yaml';
}

bool attachmentCanPreview(String? contentType) {
  return attachmentIsImageContentType(contentType) ||
      attachmentIsTextContentType(contentType) ||
      attachmentIsPdfContentType(contentType);
}

IconData attachmentPreviewIcon(String? contentType) {
  if (attachmentIsImageContentType(contentType)) return Icons.image_outlined;
  if (attachmentIsPdfContentType(contentType)) return Icons.picture_as_pdf_outlined;
  if (attachmentIsTextContentType(contentType)) return Icons.description_outlined;
  return Icons.insert_drive_file_outlined;
}
