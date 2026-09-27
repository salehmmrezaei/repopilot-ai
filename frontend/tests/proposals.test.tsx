import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ProposalView } from '../src/features/agent/ProposalView';
import { AgentPanel } from '../src/features/agent/AgentPanel';
import { getAgent } from '../src/features/agent/api';

const proposal = {
  implementation_plan: ['Update the greeting'],
  risks: ['Callers may depend on stdout'],
  test_plan: ['Assert expected output'],
  files: [
    {
      path: 'main.py',
      operation: 'replace' as const,
      before_sha256: 'b'.repeat(64),
      after_sha256: 'a'.repeat(64),
    },
  ],
  diff: '--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-old\n+<script>unsafe()</script>\n',
  diff_sha256: 'c'.repeat(64),
  validation: 'source_checked_tests_not_run' as const,
};
function json(value: unknown) {
  return new Response(JSON.stringify(value), {
    headers: { 'Content-Type': 'application/json' },
  });
}
describe('patch proposals', () => {
  it('shows plans and escaped diffs with an authorized download and untested status', () => {
    const { container } = render(
      <ProposalView runId="r1" commit={'a'.repeat(40)} proposal={proposal} />,
    );
    expect(screen.getByText(/Tests not run · Not applied/)).toBeInTheDocument();
    expect(screen.getByText('Update the greeting')).toBeInTheDocument();
    expect(screen.getByText('Assert expected output')).toBeInTheDocument();
    expect(screen.getByLabelText('Proposed diff')).toHaveTextContent(
      '<script>unsafe()</script>',
    );
    expect(container.querySelector('script')).toBeNull();
    expect(
      screen.getByRole('link', { name: 'Download patch for review' }),
    ).toHaveAttribute('href', '/api/agent-runs/r1/patch');
    expect(
      screen.queryByRole('button', { name: /apply|execute/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Imported snapshots may omit files/),
    ).toBeInTheDocument();
  });
  it('freezes proposal mode and task key across ambiguous submission retries', async () => {
    const requests: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, options: RequestInit) => {
        if (options.method === 'POST') {
          requests.push(String(options.body));
          return Promise.reject(new Error('lost response'));
        }
        return Promise.resolve(json({ enabled: true, items: [] }));
      }),
    );
    render(<AgentPanel conversationId="c1" csrf="csrf" onExpired={vi.fn()} />);
    await screen.findByText('No investigations yet.');
    fireEvent.change(screen.getByLabelText('Task type'), {
      target: { value: 'propose' },
    });
    fireEvent.change(screen.getByLabelText('Investigation task'), {
      target: { value: 'Update greeting' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Generate patch proposal' }),
    );
    await screen.findByText(/Submission uncertain/);
    expect(screen.getByLabelText('Task type')).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry same investigation' }),
    );
    await screen.findByText(/Submission uncertain/);
    expect(requests).toHaveLength(2);
    expect(requests[0]).toBe(requests[1]);
    expect(JSON.parse(requests[0]).mode).toBe('propose');
  });
  it('rejects a result claiming tests passed or applied changes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        json({
          run: {
            id: 'r1',
            conversation_id: 'c1',
            request_key: 'key',
            question: 'Task',
            mode: 'propose',
            source_index_id: 's1',
            commit_sha: 'a'.repeat(40),
            model: 'fixture',
            status: 'completed',
            error_code: null,
            error_message: null,
            created_at: '2026-09-27',
            started_at: null,
            finished_at: null,
            result: {
              answer: {
                status: 'insufficient_evidence',
                claims: [],
                limitation: 'No evidence',
              },
              evidence: [],
              proposal: { ...proposal, validation: 'tests_passed' },
            },
          },
          calls: [],
          events: [],
        }),
      ),
    );
    await expect(
      getAgent('r1', new AbortController().signal),
    ).rejects.toThrow();
  });
});
