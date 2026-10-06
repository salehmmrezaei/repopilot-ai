import { useEffect, useState } from 'react';
import { z } from 'zod';
import { requestJSON, writeHeaders } from '../../lib/api/http';
const review = z.object({
  id: z.string(),
  pull_number: z.number(),
  status: z.string(),
  base_sha: z.string().nullable(),
  head_sha: z.string().nullable(),
  error_code: z.string().nullable(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  estimated_cost_usd: z.number().nullable(),
  result: z
    .object({
      generation: z.object({
        draft: z
          .object({
            claims: z.array(
              z.object({ text: z.string(), citation_ids: z.array(z.string()) }),
            ),
            limitation: z.string(),
          })
          .nullable(),
        refused: z.boolean(),
      }),
      evidence: z.array(
        z.object({
          citation_id: z.string(),
          path: z.string(),
          commit_sha: z.string(),
          start_line: z.number(),
          end_line: z.number(),
          content: z.string(),
        }),
      ),
    })
    .nullable(),
});
export function PullReviews({
  repositoryId,
  csrf,
}: {
  repositoryId: string;
  csrf: string;
}) {
  const [items, setItems] = useState<z.infer<typeof review>[]>([]);
  const [number, setNumber] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [key, setKey] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void requestJSON(`/repositories/${repositoryId}/reviews`, {
      signal: controller.signal,
    })
      .then((v) => {
        if (!controller.signal.aborted) setItems(z.array(review).parse(v));
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError('Unable to load saved reviews.');
      });
    return () => controller.abort();
  }, [repositoryId, reload]);
  async function submit() {
    const requestKey = key ?? crypto.randomUUID();
    setKey(requestKey);
    setPending(true);
    setError('');
    try {
      const item = review.parse(
        await requestJSON(`/repositories/${repositoryId}/reviews`, {
          method: 'POST',
          headers: writeHeaders(csrf),
          body: JSON.stringify({
            pull_number: Number(number),
            request_key: requestKey,
          }),
          signal: AbortSignal.timeout(100000),
        }),
      );
      setItems((old) => [item, ...old.filter((x) => x.id !== item.id)]);
      if (item.status !== 'running') setKey(null);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : 'Review failed. Retry with the same request key to recover.',
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="status-card" aria-label="Pull request reviews">
      <h3>Pull request review</h3>
      <p>
        Reviews up to eight small changed source files at pinned commits. Uses
        one paid AI request when enabled. No code runs and no GitHub comment is
        posted.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label>
          PR number{' '}
          <input
            type="number"
            min="1"
            max="2147483647"
            required
            value={number}
            disabled={pending}
            onChange={(e) => {
              setNumber(e.target.value);
              setKey(null);
            }}
          />
        </label>
        <button disabled={pending || !number}>
          {pending ? 'Reviewing…' : key ? 'Recover review' : 'Review PR'}
        </button>
      </form>
      <button onClick={() => setReload((v) => v + 1)}>
        Reload saved reviews
      </button>
      {error && <p role="alert">{error}</p>}
      {items.map((item) => (
        <details key={item.id}>
          <summary>
            PR #{item.pull_number} · {item.status}
          </summary>
          <p>
            Base {item.base_sha} · Head {item.head_sha}
          </p>
          {item.error_code && (
            <p role="alert">
              {item.error_code.replaceAll('_', ' ')}. No review was published.
            </p>
          )}
          {item.status === 'running' && (
            <p>
              The request may still be running. Reload to recover. If the server
              interrupted it, no automatic paid retry occurs.
            </p>
          )}
          <p>
            Tokens: {item.input_tokens ?? 'unknown'} input /{' '}
            {item.output_tokens ?? 'unknown'} output · Estimated cost:{' '}
            {item.estimated_cost_usd === null
              ? 'unknown'
              : `$${item.estimated_cost_usd.toFixed(6)}`}
          </p>
          {item.result?.generation.refused && (
            <p>The model declined this review.</p>
          )}
          {item.result?.generation.draft?.claims.map((claim, i) => (
            <p key={i}>
              {claim.text} [{claim.citation_ids.join(', ')}]
            </p>
          ))}
          <p>{item.result?.generation.draft?.limitation}</p>
          {item.result?.evidence.map((e) => (
            <details key={e.citation_id}>
              <summary>
                {e.citation_id} · {e.path}:{e.start_line}–{e.end_line} ·{' '}
                {e.commit_sha.slice(0, 12)}
              </summary>
              <pre>{e.content}</pre>
            </details>
          ))}
        </details>
      ))}
    </section>
  );
}
