/**
 * Incremental Server-Sent Events parser (WHATWG HTML "event stream" rules).
 *
 * The parser is transport-only: it never interprets `data` payloads, never
 * validates envelopes and never throws on content. Callers decide what a frame
 * means. The previous chat parser only read `data:` lines and split on `\n\n`,
 * which lost `id`/`event` transport values, dropped comments, could not survive
 * a chunk boundary inside a line and dispatched a half-written final frame.
 *
 * Supported per specification:
 * - `data`, `event`, `id`, `retry` fields; unknown fields are ignored;
 * - a single optional space after the field colon is stripped;
 * - comments (`:` prefix) are liveness only and never produce a frame;
 * - multi-line `data` values are joined with `\n`;
 * - CRLF, CR and LF line terminators, including a CRLF split across chunks;
 * - dispatch happens only on an empty line;
 * - the last-event-id buffer persists across frames until another `id` field;
 * - an empty `event` value means "no event type" (frames get no `event`);
 * - a line/field buffer that spans chunks is preserved between `push` calls.
 *
 * EOF handling is a deliberate, documented extension: the canonical contract
 * requires the final frame to be emitted even when the stream ends without the
 * terminating empty line, so `end()` completes the pending line and dispatches
 * the pending frame. Without that, a stream that closes right after
 * `data: {...}\n` would silently lose its last (often terminal) event.
 */

export type SseFrame = {
  /** `data:` values joined with `\n` (per specification). */
  data: string;
  /** `event:` value of this frame; absent when the field was missing or empty. */
  event?: string;
  /**
   * `id:` value declared by *this* frame (may be an explicit empty string,
   * which the specification uses to reset the buffer).
   */
  id?: string;
  /**
   * Last-event-id buffer in effect when the frame was dispatched. The buffer
   * persists across frames, so this is what a reconnect must echo back.
   */
  lastEventId?: string;
};

export type SseParseBatch = {
  frames: SseFrame[];
  /** Comment bodies (`: text`) observed in this batch. Liveness only. */
  comments: string[];
};

/** Thrown when a caller pushes into an already finished parser. */
export class SseParserFinalizedError extends Error {
  constructor() {
    super("sse_parser_finalized");
    this.name = "SseParserFinalizedError";
  }
}

const COLON = 0x3a;
const SPACE = 0x20;
const BOM = 0xfeff;
const RETRY_DIGITS = /^[0-9]+$/;

export class SseParser {
  private line = "";
  private dataLines: string[] = [];
  private eventType = "";
  private declaredId: string | undefined;
  private eventIdBuffer: string | undefined;
  private reconnectionMs: number | undefined;
  /** Set when the previous chunk ended with CR; the next LF belongs to it. */
  private skipLeadingLf = false;
  private atStreamStart = true;
  private ended = false;
  private frames: SseFrame[] = [];
  private comments: string[] = [];

  /** Reconnection time from the last valid `retry:` field (connection-level). */
  get reconnectionTime(): number | undefined {
    return this.reconnectionMs;
  }

  /** Current last-event-id buffer (persisted across frames per specification). */
  get lastEventId(): string | undefined {
    return this.eventIdBuffer === "" ? undefined : this.eventIdBuffer;
  }

  get isEnded(): boolean {
    return this.ended;
  }

  push(chunk: string): SseParseBatch {
    if (this.ended) throw new SseParserFinalizedError();
    if (chunk.length === 0) return this.drain();

    if (this.atStreamStart) {
      this.atStreamStart = false;
      if (chunk.charCodeAt(0) === BOM) chunk = chunk.slice(1);
    }
    if (this.skipLeadingLf) {
      this.skipLeadingLf = false;
      if (chunk.startsWith("\n")) chunk = chunk.slice(1);
    }

    let index = 0;
    while (index < chunk.length) {
      let stop = index;
      while (stop < chunk.length && chunk[stop] !== "\n" && chunk[stop] !== "\r") stop += 1;
      if (stop > index) this.line += chunk.slice(index, stop);
      if (stop >= chunk.length) break;
      const terminator = chunk[stop];
      this.finishLine();
      index = stop + 1;
      if (terminator === "\r") {
        if (index >= chunk.length) this.skipLeadingLf = true;
        else if (chunk[index] === "\n") index += 1;
      }
    }
    return this.drain();
  }

  /**
   * Finish the stream: completes the pending line, dispatches the pending
   * frame (contract requires the unterminated final frame) and returns them.
   * Idempotent.
   */
  end(): SseParseBatch {
    if (this.ended) return { frames: [], comments: [] };
    this.ended = true;
    if (this.line !== "") this.finishLine();
    this.dispatch();
    return this.drain();
  }

  private drain(): SseParseBatch {
    if (this.frames.length === 0 && this.comments.length === 0) return { frames: [], comments: [] };
    const batch = { frames: this.frames, comments: this.comments };
    this.frames = [];
    this.comments = [];
    return batch;
  }

  private finishLine(): void {
    const line = this.line;
    this.line = "";
    if (line === "") {
      this.dispatch();
      return;
    }
    if (line.charCodeAt(0) === COLON) {
      const comment = line.slice(1);
      this.comments.push(comment.charCodeAt(0) === SPACE ? comment.slice(1) : comment);
      return;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1
      ? ""
      : line.charCodeAt(colon + 1) === SPACE
        ? line.slice(colon + 2)
        : line.slice(colon + 1);
    switch (field) {
      case "data":
        this.dataLines.push(value);
        return;
      case "event":
        this.eventType = value;
        return;
      case "id":
        // A NUL in an id is invalid; the whole field is ignored per specification.
        if (!value.includes("\u0000")) {
          this.eventIdBuffer = value;
          this.declaredId = value;
        }
        return;
      case "retry":
        if (RETRY_DIGITS.test(value)) {
          const parsed = Number.parseInt(value, 10);
          if (Number.isSafeInteger(parsed)) this.reconnectionMs = parsed;
        }
        return;
      default:
        // Unknown fields are ignored by specification.
        return;
    }
  }

  private dispatch(): void {
    const lines = this.dataLines;
    const eventType = this.eventType;
    const declaredId = this.declaredId;
    this.dataLines = [];
    this.eventType = "";
    this.declaredId = undefined;
    // An empty data buffer means "no event": the event type buffer is reset and
    // nothing is dispatched. The last-event-id buffer is deliberately kept.
    if (lines.length === 0) return;
    const lastEventId = this.eventIdBuffer;
    this.frames.push({
      data: lines.join("\n"),
      ...(eventType === "" ? {} : { event: eventType }),
      ...(declaredId === undefined ? {} : { id: declaredId }),
      ...(lastEventId === undefined || lastEventId === "" ? {} : { lastEventId }),
    });
  }
}

/** Convenience helper for complete buffers (fixtures, transcript replay). */
export function parseSseFrames(input: string): SseFrame[] {
  const parser = new SseParser();
  return [...parser.push(input).frames, ...parser.end().frames];
}
