import { useEffect, useState } from 'react';
import { ApiError } from '../../lib/api/http';
import { cancelAgent, getAgent, type AgentDetail } from './api';
import { consumeAgentEvents } from './events';
export function AgentRunView({
  runId,
  csrf,
  onExpired,
}: {
  runId: string;
  csrf: string;
  onExpired: () => void;
}) {
  const [data, setData] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await getAgent(runId, controller.signal);
        if (controller.signal.aborted) return;
        setData(next);
        setError(null);
      } catch (reason) {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 401) {
          onExpired();
          return;
        }
        setError('Unable to refresh investigation. Retrying…');
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
  }, [runId, onExpired, revision]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cursor = 0;
    let failures = 0;
    async function connect() {
      try {
        const outcome = await consumeAgentEvents(
          runId,
          cursor,
          (event) => {
            cursor = event.sequence;
            setRevision((v) => v + 1);
            setLive(true);
          },
          controller.signal,
        );
        failures = 0;
        if (outcome === 'complete') return;
      } catch (reason) {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 401) {
          onExpired();
          return;
        }
        setLive(false);
        failures++;
      }
      if (!controller.signal.aborted)
        timer = setTimeout(
          () => {
            void connect();
          },
          Math.min(30000, 1000 * 2 ** Math.min(failures, 5)),
        );
    }
    void connect();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, onExpired]);
  async function cancel() {
    setBusy(true);
    try {
      await cancelAgent(runId, csrf);
      setRevision((v) => v + 1);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onExpired();
      else setError('Cancellation failed. Refresh status before retrying.');
    } finally {
      setBusy(false);
    }
  }
  const unknown =
    data?.calls.filter((call) => call.input_tokens === null).length ?? 0;
  const cost =
    data?.calls.reduce(
      (total, call) => total + Number(call.cost_usd ?? 0),
      0,
    ) ?? 0;
  return (
    <section aria-label="Investigation detail">
      {!data && !error && <p role="status">Loading investigation…</p>}
      {error && <p role="alert">{error}</p>}
      {!live && (
        <p role="status">Live updates interrupted; polling saved progress.</p>
      )}
      <button onClick={() => setRevision((v) => v + 1)}>
        Refresh investigation
      </button>
      {data && (
        <>
          <h5>{data.run.question}</h5>
          <p>
            Status: {data.run.status} · Model: {data.run.model}
          </p>
          <p className="field-help">Pinned commit: {data.run.commit_sha}</p>
          {data.run.error_message && (
            <p role="alert">{data.run.error_message}</p>
          )}
          {['queued', 'running'].includes(data.run.status) && (
            <button
              disabled={busy}
              onClick={() => {
                void cancel();
              }}
            >
              Cancel investigation
            </button>
          )}
          <ol aria-label="Agent activity">
            {data.events.map((event) => (
              <li key={event.sequence}>
                <strong>{event.kind.replaceAll('_', ' ')}</strong>:{' '}
                {event.summary}
                {event.duration_ms !== null && ` (${event.duration_ms} ms)`}
              </li>
            ))}
          </ol>
          <p>
            Known estimated cost: ${cost.toFixed(6)} · Unknown calls: {unknown}.
            Unknown calls are excluded.
          </p>
          <details>
            <summary>Model call usage ({data.calls.length}/5)</summary>
            <ul>
              {data.calls.map((call) => (
                <li key={call.step}>
                  Call {call.step}:{' '}
                  {call.input_tokens === null
                    ? 'Usage unknown; charges may have occurred.'
                    : `${call.input_tokens} input / ${call.output_tokens} output tokens · $${Number(call.cost_usd).toFixed(6)}`}
                </li>
              ))}
            </ul>
          </details>
          {data.run.result && (
            <div aria-label="Investigation result">
              {data.run.result.answer.claims.map((claim, index) => (
                <p key={index}>
                  {claim.text}{' '}
                  {claim.citation_ids.map((id) => (
                    <a key={id} href={`#agent-${runId}-${id}`}>
                      [{id}]{' '}
                    </a>
                  ))}
                </p>
              ))}
              {data.run.result.answer.limitation && (
                <p>{data.run.result.answer.limitation}</p>
              )}
              {data.run.result.evidence.map((item) => (
                <details
                  key={item.citation_id}
                  id={`agent-${runId}-${item.citation_id}`}
                >
                  <summary>
                    {item.citation_id}: {item.path}:{item.start_line}–
                    {item.end_line}
                  </summary>
                  <pre>
                    <code>{item.content}</code>
                  </pre>
                </details>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
