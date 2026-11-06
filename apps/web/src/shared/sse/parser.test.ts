import { describe, expect, it } from "vitest";
import { SseParser, SseParserFinalizedError, parseSseFrames } from "./parser";
import type { SseFrame } from "./parser";

describe("SseParser", () => {
  it("parses a single LF frame with data, event and id", () => {
    const frames = parseSseFrames("id: 7\nevent: text_delta\ndata: {\"a\":1}\n\n");
    expect(frames).toEqual([
      { data: "{\"a\":1}", event: "text_delta", id: "7", lastEventId: "7" },
    ]);
  });

  it("parses CRLF frames and treats a split CRLF as one terminator", () => {
    const parser = new SseParser();
    // The CR ends one chunk and the LF opens the next: one line break, not two.
    const first = parser.push("data: one\r");
    const second = parser.push("\ndata: two\r\n\r\n");
    expect(first.frames).toEqual([]);
    expect(second.frames).toEqual([{ data: "one\ntwo" }]);
  });

  it("parses CR-only terminators", () => {
    expect(parseSseFrames("data: a\rdata: b\r\r")).toEqual([{ data: "a\nb" }]);
  });

  it("treats comments as liveness only and never as frames", () => {
    const parser = new SseParser();
    const batch = parser.push(": keepalive\ndata: {\"type\":\"x\"}\n\n");
    expect(batch.comments).toEqual(["keepalive"]);
    expect(batch.frames).toEqual([{ data: "{\"type\":\"x\"}" }]);
    // A comment inside a frame does not terminate or contribute data.
    expect(parseSseFrames("data: a\n: note\ndata: b\n\n")).toEqual([{ data: "a\nb" }]);
    // Comment-only input produces neither frames nor a cursor.
    const onlyComments = new SseParser();
    expect(onlyComments.push(": ping\n\n").frames).toEqual([]);
  });

  it("joins multi-line data with a newline and strips one leading space", () => {
    const frames = parseSseFrames("data: first\ndata:  second\ndata:third\n\n");
    expect(frames).toEqual([{ data: "first\n second\nthird" }]);
  });

  it("dispatches only on an empty line and preserves a split chunk buffer", () => {
    const parser = new SseParser();
    const first = parser.push("data: {\"type\":\"text_delta\",");
    expect(first.frames).toEqual([]);
    const second = parser.push("\"data\":{\"text\":\"hel");
    expect(second.frames).toEqual([]);
    const third = parser.push("lo\"}}\n");
    expect(third.frames).toEqual([]);
    const fourth = parser.push("\n");
    expect(fourth.frames).toEqual([{ data: "{\"type\":\"text_delta\",\"data\":{\"text\":\"hello\"}}" }]);
  });

  it("dispatches the unterminated final frame on end()", () => {
    const parser = new SseParser();
    expect(parser.push("data: {\"type\":\"_turn_complete\",\"data\":{}}").frames).toEqual([]);
    const tail = parser.end();
    expect(tail.frames).toEqual([{ data: "{\"type\":\"_turn_complete\",\"data\":{}}" }]);
    // end() is idempotent and a finished parser refuses further input.
    expect(parser.end().frames).toEqual([]);
    expect(() => parser.push("data: late\n\n")).toThrow(SseParserFinalizedError);
  });

  it("completes a pending line at EOF even without a blank separator", () => {
    const parser = new SseParser();
    parser.push("data: value\n");
    expect(parser.end().frames).toEqual([{ data: "value" }]);
  });

  it("keeps the last-event-id buffer across frames, including empty id", () => {
    const frames = parseSseFrames("id: 10\ndata: a\n\ndata: b\n\nid:\ndata: c\n\n");
    expect(frames.map((frame) => frame.id)).toEqual(["10", undefined, ""]);
    expect(frames.map((frame) => frame.lastEventId)).toEqual(["10", "10", undefined]);
    expect(frames[2].lastEventId).toBeUndefined();
  });

  it("ignores an empty event name so the frame has no type", () => {
    expect(parseSseFrames("event:\ndata: a\n\n")).toEqual([{ data: "a" }]);
    expect(parseSseFrames("event: \ndata: a\n\n")).toEqual([{ data: "a" }]);
  });

  it("ignores unknown fields and NUL ids, and keeps retry as connection state", () => {
    const parser = new SseParser();
    const batch = parser.push("foo: bar\nid: bad\u0000id\nretry: 2500\ndata: a\n\n");
    expect(batch.frames).toEqual([{ data: "a" }]);
    expect(parser.reconnectionTime).toBe(2500);
    expect(parser.lastEventId).toBeUndefined();
    // A non-numeric retry is ignored rather than coerced.
    const invalidRetry = new SseParser();
    invalidRetry.push("retry: 1.5\n\n");
    expect(invalidRetry.reconnectionTime).toBeUndefined();
  });

  it("strips a leading UTF-8 BOM only at stream start", () => {
    expect(parseSseFrames("\ufeffdata: a\n\n").map((frame) => frame.data)).toEqual(["a"]);
    expect(parseSseFrames("data: a\n\ndata: \ufeffb\n\n").map((frame) => frame.data)).toEqual(["a", "\ufeffb"]);
  });

  it("ignores empty and whitespace-only lines without data", () => {
    expect(parseSseFrames("\n\n\n")).toEqual([]);
    expect(parseSseFrames("\n\ndata: a\n\n")).toEqual([{ data: "a" }]);
  });

  it("keeps the parser incremental across a one-character chunking", () => {
    const text = [
      "id: e-1\nevent: text_delta\ndata: {\"type\":\"text_delta\",\n",
      "data: \"data\":{\"text\":\"hi\"}}\n\n",
      ": keepalive\n\n",
      "data: {\"type\":\"_turn_complete\",\"data\":{}}\n\n",
    ].join("");
    const parser = new SseParser();
    const frames: SseFrame[] = [];
    for (const char of text) frames.push(...parser.push(char).frames);
    frames.push(...parser.end().frames);
    expect(frames.map((frame) => frame.data)).toEqual([
      "{\"type\":\"text_delta\",\n\"data\":{\"text\":\"hi\"}}",
      "{\"type\":\"_turn_complete\",\"data\":{}}",
    ]);
    expect(frames[0].event).toBe("text_delta");
    expect(frames[0].id).toBe("e-1");
  });
});
