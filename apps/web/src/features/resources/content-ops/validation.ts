export const CONTENT_UPLOAD_MAX_BYTES = 100 * 1024 * 1024;

export interface ContentFileFeedback {
  mime: string;
  sizeLabel: string;
  accepted: boolean;
  message: string;
}

export function formatContentBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Size and type check for a chosen file.
 *
 * `translate` is optional because this is also called outside React (tests, the
 * upload guard). With it, the message comes from the resources unit; without it,
 * the English default is returned so the pure function stays usable on its own.
 */
export function contentFileFeedback(
  file: Pick<File, "size" | "type">,
  translate?: (key: string) => string,
): ContentFileFeedback {
  const mime = file.type.trim() || "application/octet-stream";
  const accepted = file.size <= CONTENT_UPLOAD_MAX_BYTES;
  return {
    mime,
    sizeLabel: formatContentBytes(file.size),
    accepted,
    message: accepted
      ? translate?.("resources.contentTypeAllowed") ?? "File type is not restricted by the Content API."
      : translate?.("resources.contentFileTooLarge") ?? "The Content API accepts uploads up to 100 MiB.",
  };
}
