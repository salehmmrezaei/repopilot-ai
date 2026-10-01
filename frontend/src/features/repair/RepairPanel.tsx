import { useEffect, useId, useState, type FormEvent } from 'react';
import { z } from 'zod';
import { ApiError, requestJSON, writeHeaders } from '../../lib/api/http';
import { runSchema as agentSchema } from '../agent/api';
import { ProposalView } from '../agent/ProposalView';
import { runSchema as executionSchema } from '../execution/schema';
const runSchema = z.object({
  id: z.string(),
  request_key: z.string(),
  profile: z.string(),
  max_revisions: z.number(),
  revision: z.number(),
  status: z.enum(['running', 'passed', 'exhausted', 'stopped', 'cancelled']),
  reason: z.string().nullable(),
  deadline: z.string(),
});
const listSchema = z.object({
  enabled: z.boolean(),
  items: z.array(runSchema),
});
const detailSchema = z.object({
  run: runSchema,
  attempts: z.array(
    z.object({
      agent: agentSchema,
      execution: executionSchema.nullable(),
      calls: z.array(
        z.object({
          input_tokens: z.number().nullable(),
          cost_usd: z.string().nullable(),
        }),
      ),
    }),
  ),
});
type Run = z.infer<typeof runSchema>;
type Submission = {
  request_key: string;
  profile: 'auto' | 'python' | 'javascript';
  max_revisions: number;
  confirm_automatic_repair: true;
};
export function RepairPanel({
  agentId,
  csrf,
  onExpired,
}: {
  agentId: string;
  csrf: string;
  onExpired: () => void;
}) {
  const id = useId();
  const [runs, setRuns] = useState<Run[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<z.infer<typeof detailSchema> | null>(
    null,
  );
  const [profile, setProfile] = useState<Submission['profile']>('auto');
  const [budget, setBudget] = useState(2);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState<Submission | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const options = {
          signal: AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(12000),
          ]),
        };
        const value = listSchema.parse(
          await requestJSON(`/agent-runs/${agentId}/repairs`, options),
        );
        if (controller.signal.aborted) return;
        setRuns(value.items);
        setEnabled(value.enabled);
        setPending((old) =>
          old && value.items.some((r) => r.request_key === old.request_key)
            ? null
            : old,
        );
        const chosen = selected ?? value.items[0]?.id;
        if (chosen) {
          const next = detailSchema.parse(
            await requestJSON(`/repairs/${chosen}`, options),
          );
          if (!controller.signal.aborted) setDetail(next);
        }
      } catch (reason) {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 401) {
          onExpired();
          return;
        }
        setError('Unable to load repair progress. Retrying…');
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => {
          void poll();
        }, 3000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [agentId, selected, onExpired, revision]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!confirmed || busy) return;
    const data = pending ?? {
      request_key: crypto.randomUUID(),
      profile,
      max_revisions: budget,
      confirm_automatic_repair: true as const,
    };
    setPending(data);
    setBusy(true);
    setError(null);
    try {
      const run = runSchema.parse(
        await requestJSON(`/agent-runs/${agentId}/repairs`, {
          method: 'POST',
          headers: writeHeaders(csrf),
          body: JSON.stringify(data),
        }),
      );
      setRuns((old) => [run, ...old.filter((r) => r.id !== run.id)]);
      setSelected(run.id);
      setPending(null);
      setConfirmed(false);
      setRevision((v) => v + 1);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onExpired();
      if (
        reason instanceof ApiError &&
        [400, 401, 403, 404, 409, 422, 429].includes(reason.status)
      )
        setPending(null);
      setError(
        reason instanceof ApiError
          ? reason.message
          : 'Submission uncertain. Retry the same repair key.',
      );
    } finally {
      setBusy(false);
    }
  }
  async function cancel(runId: string) {
    setBusy(true);
    try {
      await requestJSON(`/repairs/${runId}/cancel`, {
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
  const active = runs.some((r) => r.status === 'running');
  const calls = detail?.attempts.flatMap((a) => a.calls) ?? [];
  return (
    <section aria-label="Automatic repair">
      <h5>Bounded automatic repair</h5>
      <p>
        Test this proposal, then automatically generate and test up to two
        revisions. Each revision allows five model calls. The loop stops after
        20 minutes or a terminal result. Model charges may apply; test output is
        sent to the model.
      </p>
      {!enabled && <p>Automatic repair is disabled in server configuration.</p>}
      <form
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <label htmlFor={`${id}-profile`}>Repair test profile</label>
        <select
          id={`${id}-profile`}
          value={pending?.profile ?? profile}
          disabled={busy || !!pending || active}
          onChange={(event) =>
            setProfile(event.target.value as Submission['profile'])
          }
        >
          <option value="auto">Auto-detect</option>
          <option value="python">Python / pytest</option>
          <option value="javascript">JavaScript / npm test</option>
        </select>
        <label htmlFor={`${id}-budget`}>Maximum revisions</label>
        <select
          id={`${id}-budget`}
          value={pending?.max_revisions ?? budget}
          disabled={busy || !!pending || active}
          onChange={(event) => setBudget(Number(event.target.value))}
        >
          <option value={1}>1 revision / up to 5 additional calls</option>
          <option value={2}>2 revisions / up to 10 additional calls</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy || active}
            onChange={(event) => setConfirmed(event.target.checked)}
          />{' '}
          I authorize automatic model revisions and sandbox execution within
          these limits.
        </label>
        <button disabled={!enabled || !confirmed || busy || active}>
          {pending ? 'Retry same repair' : 'Start bounded repair'}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      <button onClick={() => setRevision((v) => v + 1)}>
        Refresh repair progress
      </button>
      <ul>
        {runs.map((run) => (
          <li key={run.id}>
            <button
              onClick={() => {
                setSelected(run.id);
                setDetail(null);
              }}
            >
              {run.status} · {run.revision}/{run.max_revisions} revisions ·{' '}
              {run.id}
            </button>
            {run.status === 'running' && (
              <button
                disabled={busy}
                onClick={() => {
                  void cancel(run.id);
                }}
              >
                Cancel repair
              </button>
            )}
          </li>
        ))}
      </ul>
      {detail && (
        <div aria-label="Repair history">
          <p>
            Status: {detail.run.status} · {detail.run.reason} · Deadline:{' '}
            {detail.run.deadline}
          </p>
          <p>
            Known estimated model cost (including original proposal): $
            {calls
              .reduce((sum, call) => sum + Number(call.cost_usd ?? 0), 0)
              .toFixed(6)}{' '}
            · Unknown calls: {calls.filter((c) => c.cost_usd === null).length}.
          </p>
          <p>
            Passing means the test command exited zero. Review the final diff
            and test coverage before applying it. Cancellation can leave
            in-flight charges and waits for worker cleanup.
          </p>
          {detail.attempts.map((attempt, index) => (
            <details key={attempt.agent.id}>
              <summary>
                {index === 0 ? 'Original proposal' : `Revision ${index}`} ·{' '}
                {attempt.agent.status} ·{' '}
                {attempt.execution?.status ?? 'Not executed'}
              </summary>
              <p>
                Model: {attempt.agent.model} · Agent: {attempt.agent.id}
              </p>
              {attempt.agent.error_message && (
                <p>{attempt.agent.error_message}</p>
              )}
              {attempt.agent.result?.proposal && (
                <ProposalView
                  runId={attempt.agent.id}
                  commit={attempt.agent.commit_sha}
                  proposal={attempt.agent.result.proposal}
                />
              )}
              {attempt.execution && (
                <>
                  <p>
                    Execution: {attempt.execution.id} · Patch:{' '}
                    {attempt.execution.patch_sha256} · {attempt.execution.error}
                  </p>
                  <p>
                    Image: {attempt.execution.result?.image_id} · Archive:{' '}
                    {attempt.execution.result?.archive_sha256}
                  </p>
                  {(['baseline', 'patched'] as const).map((phase) => {
                    const receipt = attempt.execution?.result?.[phase];
                    return (
                      receipt && (
                        <section key={phase}>
                          <h6>
                            {phase}: {receipt.status} · Exit{' '}
                            {receipt.exit_code ?? 'unknown'}
                          </h6>
                          {receipt.truncated && <p>Output truncated.</p>}
                          <pre>
                            <code>{receipt.log}</code>
                          </pre>
                        </section>
                      )
                    );
                  })}
                </>
              )}
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
