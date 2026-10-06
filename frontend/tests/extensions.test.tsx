import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { GitHubConnection } from '../src/features/auth/GitHubConnection';
import { PullReviews } from '../src/features/reviews/PullReviews';
const json = (data: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify(data), {
      headers: { 'Content-Type': 'application/json' },
    }),
  );
describe('Milestone 12 extensions', () => {
  it('shows private access scope and disconnects with CSRF', async () => {
    const fetcher = vi.fn((url: string, options?: RequestInit) => {
      if (url.endsWith('/connection')) {
        expect(options?.method).toBe('DELETE');
        expect(options?.headers).toMatchObject({ 'X-CSRF-Token': 'proof' });
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return json({
        enabled: true,
        connected: true,
        login: 'octocat',
        private_access: true,
      });
    });
    vi.stubGlobal('fetch', fetcher);
    render(<GitHubConnection csrf="proof" />);
    await screen.findByText('Private repository permission granted.');
    fireEvent.click(screen.getByLabelText('Allow private repository access'));
    expect(
      screen.getByText(/permission includes write access/),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Disconnect repository access' }),
    );
    await screen.findByRole('button', { name: 'Connect GitHub' });
  });
  it('rejects an unexpected OAuth navigation target', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        url.endsWith('/status')
          ? json({
              enabled: true,
              connected: false,
              login: null,
              private_access: false,
            })
          : json({ url: 'https://evil.example/authorize' }),
      ),
    );
    render(<GitHubConnection />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with GitHub' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Invalid authorization URL',
    );
  });
  it('submits one idempotent PR review and escapes evidence', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, options?: RequestInit) => {
        if (options?.method !== 'POST') return json([]);
        calls += 1;
        expect(options.headers).toMatchObject({ 'X-CSRF-Token': 'proof' });
        const body = JSON.parse(String(options.body));
        expect(body.pull_number).toBe(12);
        expect(body.request_key).toMatch(/^[a-f0-9-]{36}$/);
        return json({
          id: 'review',
          pull_number: 12,
          status: 'completed',
          base_sha: 'a'.repeat(40),
          head_sha: 'b'.repeat(40),
          error_code: null,
          input_tokens: 10,
          output_tokens: 5,
          estimated_cost_usd: 0.001,
          result: {
            generation: {
              refused: false,
              draft: {
                claims: [{ text: 'Potential defect', citation_ids: ['E1'] }],
                limitation: 'Tests were not run.',
              },
            },
            evidence: [
              {
                citation_id: 'E1',
                path: 'a.ts',
                commit_sha: 'b'.repeat(40),
                start_line: 1,
                end_line: 1,
                content: '<script>unsafe()</script>',
              },
            ],
          },
        });
      }),
    );
    const { container } = render(
      <PullReviews repositoryId="repo" csrf="proof" />,
    );
    fireEvent.change(screen.getByLabelText('PR number'), {
      target: { value: '12' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Review PR' }));
    expect(
      await screen.findByText('Potential defect [E1]'),
    ).toBeInTheDocument();
    expect(screen.getByText('<script>unsafe()</script>')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(calls).toBe(1);
  });
});
