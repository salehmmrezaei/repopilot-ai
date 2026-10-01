import { useEffect, useId, useState, type FormEvent } from 'react';
import { z } from 'zod';
import { ApiError, requestJSON, writeHeaders } from '../../lib/api/http';
import { runSchema } from './schema';
const listSchema = z.object({
  enabled: z.boolean(),
  items: z.array(runSchema),
});
type Run = z.infer<typeof runSchema>;
type Profile = 'auto' | 'python' | 'javascript';
export function ExecutionPanel({
  agentId,
  csrf,
  onExpired,
  onRevise,
}: {
  agentId: string;
  csrf: string;
  onExpired: () => void;
  onRevise?: (id: string) => void;
}) {
  const id = useId();
  const [runs, setRuns] = useState<Run[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile>('auto');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [pending, setPending] = useState<{
    request_key: string;
    profile: Profile;
    confirm_execution: true;
  } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const value = listSchema.parse(
          await requestJSON(`/agent-runs/${agentId}/executions`, {
            signal: AbortSignal.any([
              controller.signal,
              AbortSignal.timeout(12000),
            ]),
          }),
        );
        if (controller.signal.aborted) return;
        setRuns(value.items);
        setEnabled(value.enabled);
        setPending((old) =>
          old && value.items.some((r) => r.request_key === old.request_key)
            ? null
            : old,
        );
      } catch (reason) {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 401) {
          onExpired();
          return;
        }
        setError('Unable to load sandbox runs. Retrying…');
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => {
          void poll();
        }, 2000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [agentId, onExpired, revision]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !confirmed) return;
    const data = pending ?? {
      request_key: crypto.randomUUID(),
      profile,
      confirm_execution: true as const,
    };
    setPending(data);
    setBusy(true);
    setError(null);
    try {
      const value = runSchema.parse(
        await requestJSON(`/agent-runs/${agentId}/executions`, {
          method: 'POST',
          headers: writeHeaders(csrf),
          body: JSON.stringify(data),
        }),
      );
      setRuns((old) => [value, ...old.filter((r) => r.id !== value.id)]);
      setPending(null);
      setConfirmed(false);
      setRevision((v) => v + 1);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onExpired();
      if (
        reason instanceof ApiError &&
        [400, 401, 403, 404, 422, 429].includes(reason.status)
      )
        setPending(null);
      setError(
        reason instanceof ApiError
          ? reason.message
          : 'Submission uncertain. Retry the same execution key.',
      );
    } finally {
      setBusy(false);
    }
  }
  async function cancel(runId: string) {
    setBusy(true);
    try {
      await requestJSON(`/executions/${runId}/cancel`, {
        method: 'POST',
        headers: writeHeaders(csrf),
        body: '{}',
      });
      setRevision((v) => v + 1);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onExpired();
      else setError('Cancellation failed. Refresh before retrying.');
    } finally {
      setBusy(false);
    }
  }
  const active = runs.some((r) => ['queued', 'running'].includes(r.status));
  return (
    <section aria-label="Sandbox validation">
      <h5>Validate in an isolated sandbox</h5>
      <p>
        Runs repository test code against the pinned archive and proposed patch
        in separate baseline and patched containers. Network and runtime
        dependency installation are disabled. The original repository is
        unchanged.
      </p>
      {!enabled && (
        <p>Sandbox execution is disabled in server configuration.</p>
      )}
      <form
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <label htmlFor={`${id}-profile`}>Test profile</label>
        <select
          id={`${id}-profile`}
          value={pending?.profile ?? profile}
          disabled={busy || !!pending || active}
          onChange={(event) => setProfile(event.target.value as Profile)}
        >
          <option value="auto">Auto-detect at repository root</option>
          <option value="python">Python / pytest</option>
          <option value="javascript">JavaScript / npm test</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            disabled={busy || active}
          />{' '}
          I reviewed this patch and want to run repository code in the sandbox.
        </label>
        <button disabled={!enabled || !confirmed || busy || active}>
          {pending ? 'Retry same execution' : 'Run isolated validation'}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      <button onClick={() => setRevision((v) => v + 1)}>
        Refresh execution results
      </button>
      {runs.map((run) => (
        <details key={run.id} open={active}>
          <summary>
            {run.status} · {run.stage} · {run.profile}
          </summary>
          <p>
            Execution: {run.id} · Patch SHA-256: {run.patch_sha256}
          </p>
          {run.error && <p role="alert">{run.error}</p>}
          {['queued', 'running'].includes(run.status) && (
            <button
              disabled={busy}
              onClick={() => {
                void cancel(run.id);
              }}
            >
              Cancel execution
            </button>
          )}
          {run.result && (
            <>
              <p>
                Image: {run.result.image_id} · Archive SHA-256:{' '}
                {run.result.archive_sha256}
              </p>
              <p>Command: {run.result.command.join(' ')}</p>
              <p>
                Exit zero reports command success; it does not prove coverage or
                correctness. A failing baseline may indicate missing
                dependencies or an existing defect.
              </p>
              {(['preparation', 'baseline', 'patched'] as const).map(
                (phase) => {
                  const result = run.result?.[phase];
                  return (
                    result && (
                      <section key={phase} aria-label={`${phase} result`}>
                        <h6>{phase}</h6>
                        <p>
                          {result.status} · Exit:{' '}
                          {result.exit_code ?? 'unknown'} · {result.duration_ms}{' '}
                          ms
                        </p>
                        {result.truncated && <p>Output truncated.</p>}
                        <pre>
                          <code>{result.log}</code>
                        </pre>
                      </section>
                    )
                  );
                },
              )}
              {onRevise && ['completed', 'failed'].includes(run.status) && (
                <button
                  title="This prepares a new AI proposal using the saved logs; model charges may apply."
                  onClick={() => onRevise(run.id)}
                >
                  Use results in a revision proposal
                </button>
              )}
            </>
          )}
        </details>
      ))}
    </section>
  );
}
