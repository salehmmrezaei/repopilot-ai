import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ExecutionPanel } from '../src/features/execution/ExecutionPanel';
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { 'Content-Type': 'application/json' },
  });
const command = {
  status: 'failed',
  exit_code: 1,
  log: '<script>malicious()</script>\nmissing dependency',
  truncated: true,
  duration_ms: 23,
};
const run = {
  id: 'e1',
  request_key: 'key',
  profile: 'python',
  status: 'completed',
  stage: 'finished',
  error: null,
  patch_sha256: 'a'.repeat(64),
  result: {
    image_id: 'sha256:' + 'b'.repeat(64),
    archive_sha256: 'c'.repeat(64),
    profile: 'python',
    command: ['pytest'],
    preparation: { ...command, status: 'passed', exit_code: 0 },
    baseline: command,
    patched: { ...command, status: 'passed', exit_code: 0 },
  },
};
describe('sandbox execution', () => {
  it('requires explicit confirmation and preserves request identity on uncertain submission', async () => {
    const requests: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, options: RequestInit) => {
        if (options.method === 'POST') {
          requests.push(String(options.body));
          return Promise.reject(new Error('response lost'));
        }
        return Promise.resolve(json({ enabled: true, items: [] }));
      }),
    );
    render(<ExecutionPanel agentId="a1" csrf="csrf" onExpired={vi.fn()} />);
    const checkbox = screen.getByRole('checkbox');
    expect(
      screen.getByRole('button', { name: 'Run isolated validation' }),
    ).toBeDisabled();
    fireEvent.click(checkbox);
    // Wait for the initial enabled flag.
    const button = await screen.findByRole('button', {
      name: 'Run isolated validation',
    });
    await vi.waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Retry same execution' }),
    );
    await screen.findByText(/Submission uncertain/);
    expect(requests).toHaveLength(2);
    expect(requests[0]).toBe(requests[1]);
    expect(JSON.parse(requests[0]).confirm_execution).toBe(true);
  });
  it('separates baseline and patched receipts, escapes logs and offers reviewed revision', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(json({ enabled: true, items: [run] })),
    );
    const revise = vi.fn();
    const { container } = render(
      <ExecutionPanel
        agentId="a1"
        csrf="csrf"
        onExpired={vi.fn()}
        onRevise={revise}
      />,
    );
    await screen.findByText(/completed · finished/);
    fireEvent.click(screen.getByText(/completed · finished/));
    expect(
      screen.getByRole('region', { name: 'baseline result' }),
    ).toHaveTextContent('failed');
    expect(
      screen.getByRole('region', { name: 'patched result' }),
    ).toHaveTextContent('passed');
    expect(screen.getByText(/does not prove coverage/)).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Use results in a revision proposal',
      }),
    );
    expect(revise).toHaveBeenCalledWith('e1');
  });
  it('disables launching while a run is active and sends cancellation with writer headers', async () => {
    const fetcher = vi
      .fn()
      .mockImplementation((_url: string, options: RequestInit) =>
        Promise.resolve(
          json(
            options.method === 'POST'
              ? { ...run, status: 'cancelled' }
              : {
                  enabled: true,
                  items: [{ ...run, status: 'running', stage: 'baseline' }],
                },
          ),
        ),
      );
    vi.stubGlobal('fetch', fetcher);
    render(<ExecutionPanel agentId="a1" csrf="csrf" onExpired={vi.fn()} />);
    const cancel = await screen.findByRole('button', {
      name: 'Cancel execution',
    });
    expect(screen.getByRole('checkbox')).toBeDisabled();
    fireEvent.click(cancel);
    await vi.waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        '/api/executions/e1/cancel',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
  });
});
