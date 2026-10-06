import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { IndexInspector } from '../src/features/indexing/IndexInspector';
const job = {
  id: 'index-1',
  status: 'queued',
  stage: 'queued',
  commit_sha: 'a'.repeat(40),
  pipeline_version: 'test',
  files_stored: 1,
  symbol_count: 1,
  chunk_count: 1,
  error_message: null,
  diagnostics: [],
};
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
describe('source index', () => {
  it('queues indexing with CSRF and then offers cancellation', async () => {
    let created = false;
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          expect(options.headers).toMatchObject({ 'X-CSRF-Token': 'proof' });
          created = true;
          return Promise.resolve(json(job, 202));
        }
        return Promise.resolve(
          json({ latest: created ? job : null, active: null }),
        );
      }),
    );
    render(
      <IndexInspector
        repositoryId="repo"
        fileId={null}
        csrf="proof"
        onExpired={() => {}}
      />,
    );
    await screen.findByText(/Build a static index/);
    fireEvent.click(screen.getByRole('button', { name: 'Build index' }));
    expect(
      await screen.findByRole('button', { name: 'Cancel indexing' }),
    ).toBeInTheDocument();
  });
  it('shows source ranges, escaped chunks, and parsing diagnostics', async () => {
    const active = {
      ...job,
      status: 'completed',
      stage: 'completed',
      diagnostics: [
        {
          file_id: 'file',
          path: 'app.py',
          message: 'syntax_error: using text chunks',
        },
      ],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(
          json(
            url.includes('/files/')
              ? {
                  index_id: job.id,
                  file_id: 'file',
                  path: 'app.py',
                  language: 'python',
                  commit_sha: job.commit_sha,
                  symbols: [
                    {
                      id: 's',
                      ordinal: 0,
                      qualified_name: 'Service.run',
                      kind: 'method',
                      start_line: 2,
                      end_line: 4,
                    },
                  ],
                  chunks: [
                    {
                      id: 'c',
                      ordinal: 0,
                      kind: 'fallback',
                      start_line: 1,
                      end_line: 1,
                      start_offset: 0,
                      end_offset: 8,
                      content: '<script>untrusted</script>',
                    },
                  ],
                  next_symbol_offset: null,
                  next_chunk_offset: null,
                }
              : { latest: active, active },
          ),
        ),
      ),
    );
    const { container } = render(
      <IndexInspector
        repositoryId="repo"
        fileId="file"
        csrf="proof"
        onExpired={() => {}}
      />,
    );
    expect(await screen.findByText('Service.run')).toBeInTheDocument();
    expect(screen.getByText('method · L2–4')).toBeInTheDocument();
    expect(screen.getByText('<script>untrusted</script>')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('1 parsing diagnostics')).toBeInTheDocument();
  });
  it('shows actionable start failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, options?: RequestInit) =>
        Promise.resolve(
          options?.method === 'POST'
            ? json(
                {
                  error: { message: 'Complete the repository import first.' },
                },
                409,
              )
            : json({ latest: null, active: null }),
        ),
      ),
    );
    render(
      <IndexInspector
        repositoryId="repo"
        fileId={null}
        csrf="proof"
        onExpired={() => {}}
      />,
    );
    await screen.findByText(/Build a static index/);
    fireEvent.click(screen.getByRole('button', { name: 'Build index' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Complete the repository import first.',
    );
  });
});

it('offers a parser upgrade without hiding the previous snapshot', async () => {
  const active = { ...job, status: 'completed', stage: 'completed' };
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      json({
        latest: active,
        active,
        rebuild_available: true,
        current_pipeline_version: 'new-parser',
      }),
    ),
  );
  render(
    <IndexInspector
      repositoryId="repo"
      fileId={null}
      csrf="proof"
      onExpired={() => {}}
    />,
  );
  expect(
    await screen.findByRole('button', { name: 'Rebuild index' }),
  ).toBeEnabled();
  expect(screen.getByText(/A newer parser is available/)).toHaveTextContent(
    'prepare search again',
  );
  expect(screen.getByText(/1 files/)).toHaveTextContent('1 symbols');
});

it('shows TSX symbols, declaration metadata, and escaped documentation', async () => {
  const active = { ...job, status: 'completed', stage: 'completed' };
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        json(
          url.includes('/files/')
            ? {
                index_id: job.id,
                file_id: 'file',
                path: 'Card.tsx',
                language: 'typescript',
                commit_sha: job.commit_sha,
                symbols: [
                  {
                    id: 's1',
                    ordinal: 0,
                    qualified_name: 'CardProps',
                    kind: 'interface',
                    start_line: 1,
                    end_line: 3,
                    signature: 'export interface CardProps',
                    docstring: '/** <script>untrusted()</script> */',
                  },
                ],
                chunks: [],
                next_symbol_offset: null,
                next_chunk_offset: null,
              }
            : { latest: active, active },
        ),
      ),
    ),
  );
  const { container } = render(
    <IndexInspector
      repositoryId="repo"
      fileId="file"
      csrf="proof"
      onExpired={() => {}}
    />,
  );
  expect(await screen.findByText('CardProps')).toBeInTheDocument();
  expect(screen.getByText(/TypeScript \/ TSX · Commit/)).toBeInTheDocument();
  expect(screen.getByText('interface · L1–3')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Declaration details'));
  expect(screen.getByText('export interface CardProps')).toBeInTheDocument();
  expect(
    screen.getByText('/** <script>untrusted()</script> */'),
  ).toBeInTheDocument();
  expect(container.querySelector('script')).toBeNull();
});
