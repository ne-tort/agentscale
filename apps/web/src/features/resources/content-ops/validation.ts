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

export function contentFileFeedback(file: Pick<File, "size" | "type">): ContentFileFeedback {
  const mime = file.type.trim() || "application/octet-stream";
  const accepted = file.size <= CONTENT_UPLOAD_MAX_BYTES;
  return {
    mime,
    sizeLabel: formatContentBytes(file.size),
    accepted,
    message: accepted ? "File type is not restricted by the Content API." : "The Content API accepts uploads up to 100 MiB.",
  };
}
