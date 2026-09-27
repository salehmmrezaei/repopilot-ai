import { ApiError } from '../../lib/api/http';
import { eventSchema, type AgentEvent } from './api';
export async function consumeAgentEvents(
  id: string,
  cursor: number,
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
): Promise<'complete' | 'reconnect'> {
  const response = await fetch(
    `/api/agent-runs/${encodeURIComponent(id)}/events`,
    {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        Accept: 'text/event-stream',
        'X-RepoPilot-Request': '1',
        'Last-Event-ID': String(cursor),
      },
      signal: AbortSignal.any([signal, AbortSignal.timeout(35000)]),
    },
  );
  if (!response.ok)
    throw new ApiError(
      response.status,
      'Unable to connect to investigation events.',
    );
  if (
    !response.body ||
    !response.headers.get('content-type')?.startsWith('text/event-stream')
  )
    throw new Error('Expected event stream');
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '';
  let bytes = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) throw new Error('Truncated event stream');
      bytes += part.value.byteLength;
      if (bytes > 128 * 1024) throw new Error('Stream too large');
      buffer += decoder.decode(part.value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n');
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (frame.length > 8192) throw new Error('Event too large');
        const lines = frame.split('\n');
        const field = (name: string) =>
          lines
            .find((line) => line.startsWith(name + ':'))
            ?.slice(name.length + 1)
            .trim() ?? '';
        const type = field('event');
        if (type === 'stream.end') return 'complete';
        if (type === 'stream.reconnect') return 'reconnect';
        if (type === 'stream.error') {
          const value: unknown = JSON.parse(field('data'));
          if (
            typeof value === 'object' &&
            value !== null &&
            'code' in value &&
            value.code === 'unauthenticated'
          )
            throw new ApiError(401, 'Session expired');
          throw new Error('Stream unavailable');
        }
        if (type === 'agent.action') {
          const event = eventSchema.parse(JSON.parse(field('data')));
          if (field('id') !== String(event.sequence))
            throw new Error('Invalid sequence');
          if (event.sequence <= cursor) continue;
          if (event.sequence !== cursor + 1) throw new Error('Missing event');
          cursor = event.sequence;
          onEvent(event);
        }
      }
      if (buffer.length > 8192) throw new Error('Event too large');
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
