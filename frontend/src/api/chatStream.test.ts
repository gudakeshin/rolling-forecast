import { describe, expect, it } from 'vitest';
import { createSSEParser, type SSEEvent } from './chat';

function parse(chunks: string[]): SSEEvent[] {
  const out: SSEEvent[] = [];
  const p = createSSEParser((e) => out.push(e));
  chunks.forEach((c) => p.push(c));
  p.end();
  return out;
}

describe('createSSEParser', () => {
  it('joins multi-line data fields instead of keeping the last line', () => {
    const events = parse(['event: message_end\ndata: {"content":\ndata: "two lines"}\n\n']);
    expect(events).toEqual([{ event: 'message_end', data: { content: 'two lines' } }]);
  });

  it('handles CRLF, no space after the colon, comments and split chunks', () => {
    const events = parse([': keep-alive\r\n', 'event:token\r\nda', 'ta:{"text":"hi"}\r\n\r\n']);
    expect(events).toEqual([{ event: 'token', data: { text: 'hi' } }]);
  });

  it('defaults the event name to message and flushes a final event at stream end', () => {
    expect(parse(['data: {"a":1}'])).toEqual([{ event: 'message', data: { a: 1 } }]);
  });

  it('skips malformed JSON without stopping the stream', () => {
    const events = parse(['event: token\ndata: {oops\n\nevent: token\ndata: {"text":"ok"}\n\n']);
    expect(events).toEqual([{ event: 'token', data: { text: 'ok' } }]);
  });
});
