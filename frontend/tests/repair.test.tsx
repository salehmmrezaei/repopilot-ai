import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RepairPanel } from '../src/features/repair/RepairPanel';
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { 'Content-Type': 'application/json' },
  });
const run = {
  id: 'r1',
  request_key: 'key',
  profile: 'python',
  max_revisions: 2,
  revision: 0,
  status: 'running',
  reason: null,
  deadline: '2026-09-30T12:00:00Z',
};
describe('bounded repair', () => {
  it('requires consent and freezes limits and identity across an uncertain retry', async () => {
    const requests: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, options: RequestInit) => {
        if (options.method === 'POST') {
          requests.push(String(options.body));
          return Promise.reject(new Error('lost'));
        }
        return Promise.resolve(json({ enabled: true, items: [] }));
      }),
    );
    render(<RepairPanel agentId="a1" csrf="csrf" onExpired={vi.fn()} />);
    const button = screen.getByRole('button', { name: 'Start bounded repair' });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Maximum revisions'), {
      target: { value: '1' },
    });
    fireEvent.click(screen.getByRole('checkbox'));
    await vi.waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const retry = await screen.findByRole('button', {
      name: 'Retry same repair',
    });
    await vi.waitFor(() => expect(retry).toBeEnabled());
    expect(screen.getByLabelText('Maximum revisions')).toBeDisabled();
    fireEvent.click(retry);
    await vi.waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[0]).toEqual(requests[1]);
    expect(JSON.parse(requests[0])).toMatchObject({
      max_revisions: 1,
      confirm_automatic_repair: true,
    });
  });
  it('recovers persisted runs and cancels with a protected write', async () => {
    const fetch = vi
      .fn()
      .mockImplementation((url: string, options: RequestInit) => {
        if (options.method === 'POST')
          return Promise.resolve(json({ ...run, status: 'cancelled' }));
        if (url.endsWith('/repairs/r1'))
          return Promise.resolve(json({ run, attempts: [] }));
        return Promise.resolve(json({ enabled: true, items: [run] }));
      });
    vi.stubGlobal('fetch', fetch);
    render(<RepairPanel agentId="a1" csrf="csrf" onExpired={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Cancel repair' }),
    );
    await vi.waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/repairs/r1/cancel',
        expect.objectContaining({ method: 'POST', body: '{}' }),
      ),
    );
    expect(
      screen.getByRole('button', { name: 'Start bounded repair' }),
    ).toBeDisabled();
  });
  it('renders terminal outcomes and unknown costs without claiming correctness', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(
          json(
            url.endsWith('/repairs/r1')
              ? {
                  run: {
                    ...run,
                    status: 'exhausted',
                    reason: '<script>attack</script>',
                  },
                  attempts: [],
                }
              : { enabled: false, items: [{ ...run, status: 'exhausted' }] },
          ),
        ),
      ),
    );
    const { container } = render(
      <RepairPanel agentId="a1" csrf="csrf" onExpired={vi.fn()} />,
    );
    expect(await screen.findByText(/Status: exhausted/)).toHaveTextContent(
      '<script>attack</script>',
    );
    expect(container.querySelector('script')).toBeNull();
    expect(
      screen.getByText(/Passing means the test command exited zero/),
    ).toBeInTheDocument();
  });
});
