import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AgentRunView } from '../src/features/agent/AgentRunView';
import { AgentPanel } from '../src/features/agent/AgentPanel';
import { consumeAgentEvents } from '../src/features/agent/events';
const run = {
  id: 'r1',
  conversation_id: 'c1',
  request_key: 'key',
  question: 'Explain hello',
  source_index_id: 's1',
  commit_sha: 'a'.repeat(40),
  model: 'fixture',
  status: 'completed',
  error_code: null,
  error_message: null,
  created_at: '2026-09-26',
  started_at: null,
  finished_at: null,
  result: {
    answer: {
      status: 'answered',
      claims: [{ text: 'Prints hello.', citation_ids: ['C1'] }],
      limitation: '',
    },
    evidence: [
      {
        citation_id: 'C1',
        chunk_id: 'chunk',
        path: 'main.py',
        commit_sha: 'a'.repeat(40),
        start_line: 1,
        end_line: 1,
        content: '<script>untrusted()</script>',
      },
    ],
  },
};
const event = {
  sequence: 1,
  kind: 'tool_completed',
  summary: 'Read main.py',
  tool: 'read_file',
  duration_ms: 2,
  created_at: '2026-09-26',
};
function stream(text: string) {
  return new Response(text, {
    headers: { 'Content-Type': 'text/event-stream' },
  });
}
function json(value: unknown) {
  return new Response(JSON.stringify(value), {
    headers: { 'Content-Type': 'application/json' },
  });
}
describe('read-only investigations', () => {
  it('shows grounded snapshots, actions and unknown usage without rendering repository HTML', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) =>
        url.endsWith('/events')
          ? Promise.resolve(stream('event: stream.end\ndata: {}\n\n'))
          : Promise.resolve(
              json({
                run,
                events: [event],
                calls: [
                  {
                    step: 1,
                    input_rate: '0.4',
                    output_rate: '1.6',
                    input_tokens: null,
                    output_tokens: null,
                    cost_usd: null,
                    created_at: '2026-09-26',
                    finished_at: null,
                  },
                ],
              }),
            ),
      ),
    );
    const { container } = render(
      <AgentRunView runId="r1" csrf="csrf" onExpired={vi.fn()} />,
    );
    expect(await screen.findByText('Prints hello.')).toBeInTheDocument();
    expect(
      screen.getByRole('list', { name: 'Agent activity' }),
    ).toHaveTextContent('Read main.py');
    expect(screen.getByText(/Unknown calls: 1/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '[C1]' })).toHaveAttribute(
      'href',
      '#agent-r1-C1',
    );
    expect(container.querySelector('script')).toBeNull();
    expect(
      screen.getByText('<script>untrusted()</script>'),
    ).toBeInTheDocument();
  });
  it('keeps the same request key when submission response is lost', async () => {
    const bodies: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, options: RequestInit) => {
        if (options.method === 'POST') {
          bodies.push(String(options.body));
          return Promise.reject(new Error('lost response'));
        }
        return Promise.resolve(json({ enabled: true, items: [] }));
      }),
    );
    render(<AgentPanel conversationId="c1" csrf="csrf" onExpired={vi.fn()} />);
    await screen.findByText('No investigations yet.');
    fireEvent.change(screen.getByLabelText('Investigation task'), {
      target: { value: 'Explain the greeting' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Start investigation' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Retry same investigation' }),
    );
    await screen.findByText(/Submission uncertain/);
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toBe(bodies[1]);
  });
  it('replays only new events and sends the cursor header', async () => {
    const next = { ...event, sequence: 2 };
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        stream(
          `id: 2\nevent: agent.action\ndata: ${JSON.stringify(next)}\n\nevent: stream.end\ndata: {}\n\n`,
        ),
      );
    vi.stubGlobal('fetch', fetcher);
    const received = vi.fn();
    expect(
      await consumeAgentEvents('r1', 1, received, new AbortController().signal),
    ).toBe('complete');
    expect(received).toHaveBeenCalledWith(next);
    expect(fetcher.mock.calls[0][1].headers['Last-Event-ID']).toBe('1');
  });
  it('rejects gaps in event sequence', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          stream(
            `id: 3\nevent: agent.action\ndata: ${JSON.stringify({ ...event, sequence: 3 })}\n\n`,
          ),
        ),
    );
    await expect(
      consumeAgentEvents('r1', 0, vi.fn(), new AbortController().signal),
    ).rejects.toThrow('Missing event');
  });
});
