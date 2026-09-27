import { useEffect, useId, useState, type FormEvent } from 'react';
import { ApiError } from '../../lib/api/http';
import { listAgents, submitAgent, type AgentRun, type Submission } from './api';
import { AgentRunView } from './AgentRunView';
export function AgentPanel({
  conversationId,
  csrf,
  onExpired,
}: {
  conversationId: string;
  csrf: string;
  onExpired: () => void;
}) {
  const id = useId();
  const [items, setItems] = useState<AgentRun[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState<'investigate' | 'propose'>('investigate');
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Submission | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const value = await listAgents(conversationId, controller.signal);
        if (controller.signal.aborted) return;
        setItems(value.items);
        setEnabled(value.enabled);
        setLoading(false);
        setError(null);
        setPending((previous) =>
          previous &&
          value.items.some((item) => item.request_key === previous.request_key)
            ? null
            : previous,
        );
      } catch (reason) {
        if (controller.signal.aborted) return;
        setLoading(false);
        if (reason instanceof ApiError && reason.status === 401) {
          onExpired();
          return;
        }
        setError('Unable to load investigations. Retrying…');
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => {
          void poll();
        }, 5000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [conversationId, onExpired, revision]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const request = pending ?? {
      request_key: crypto.randomUUID(),
      question: question.trim(),
      mode,
    };
    setPending(request);
    setBusy(true);
    try {
      const run = await submitAgent(conversationId, request, csrf);
      setSelected(run.id);
      setItems((previous) =>
        [run, ...previous.filter((item) => item.id !== run.id)].slice(0, 20),
      );
      setPending(null);
      setQuestion('');
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
          : 'Submission uncertain. Retry the same task key to avoid duplicates.',
      );
    } finally {
      setBusy(false);
    }
  }
  const active = items.some((run) =>
    ['queued', 'running'].includes(run.status),
  );
  const visible = selected ?? items[0]?.id;
  return (
    <section className="index-inspector" aria-label="Read-only investigations">
      <h4>Investigate or propose changes</h4>
      <p>
        The agent can search code, read files, find symbols and locate literal
        references, then suggest an implementation plan and patch. Up to five
        paid model calls. Patches are review drafts; code is never applied or
        executed. Repository evidence is sent to the configured provider.
      </p>
      {loading && <p role="status">Loading investigations…</p>}
      {!loading && !enabled && (
        <p>Investigations are disabled in server configuration.</p>
      )}
      <form
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <label htmlFor={`${id}-mode`}>Task type</label>
        <select
          id={`${id}-mode`}
          value={pending?.mode ?? mode}
          disabled={busy || !!pending || active}
          onChange={(event) =>
            setMode(event.target.value as 'investigate' | 'propose')
          }
        >
          <option value="investigate">Read-only investigation</option>
          <option value="propose">Implementation plan and patch</option>
        </select>
        <label htmlFor={`${id}-task`}>Investigation task</label>
        <textarea
          id={`${id}-task`}
          required
          maxLength={512}
          rows={3}
          value={pending?.question ?? question}
          disabled={busy || !!pending || active}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button
          disabled={
            busy ||
            loading ||
            !enabled ||
            active ||
            (!pending && !question.trim())
          }
        >
          {busy
            ? 'Queuing…'
            : pending
              ? 'Retry same investigation'
              : mode === 'propose'
                ? 'Generate patch proposal'
                : 'Start investigation'}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {!loading && items.length === 0 && <p>No investigations yet.</p>}
      {items.length > 0 && (
        <>
          <label htmlFor={`${id}-runs`}>Recent investigations</label>
          <select
            id={`${id}-runs`}
            value={visible}
            onChange={(event) => setSelected(event.target.value)}
          >
            {items.map((run) => (
              <option key={run.id} value={run.id}>
                {run.mode === 'propose' ? 'Proposal' : 'Investigation'} ·{' '}
                {run.status} · {run.question}
              </option>
            ))}
          </select>
        </>
      )}
      {visible && (
        <AgentRunView
          key={visible}
          runId={visible}
          csrf={csrf}
          onExpired={onExpired}
        />
      )}
    </section>
  );
}
