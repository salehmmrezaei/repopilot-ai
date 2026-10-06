import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../../lib/api/http';
import {
  cancelSearch,
  getSearchState,
  prepareSearch,
  querySearch,
  type SearchMode,
  type SearchResult,
  type SearchState,
} from './api';
import { SearchResults } from './SearchResults';
interface Props {
  repositoryId: string;
  csrf: string;
  onExpired: () => void;
}
export function RepositorySearch({ repositoryId, csrf, onExpired }: Props) {
  const [state, setState] = useState<SearchState | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [pending, setPending] = useState(false);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState('');
  const [rerank, setRerank] = useState(false);
  const [mode, setMode] = useState<SearchMode>('keyword');
  const [result, setResult] = useState<SearchResult | null>(null);
  const request = useRef<AbortController | null>(null);
  const running =
    state?.latest && ['queued', 'running'].includes(state.latest.status);
  const ready = mode === 'keyword' ? state?.keyword : state?.hybrid;
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      let delay = 5000;
      try {
        const next = await getSearchState(repositoryId, controller.signal);
        if (!active) return;
        setState(next);
        setStatusError(null);
        delay =
          next.latest && ['queued', 'running'].includes(next.latest.status)
            ? 2000
            : 15000;
      } catch (reason) {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 401) {
          onExpired();
          return;
        }
        setStatusError(
          reason instanceof ApiError
            ? reason.message
            : 'Unable to read search status. Retrying…',
        );
      }
      if (active)
        timer = setTimeout(() => {
          void poll();
        }, delay);
    }
    void poll();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [repositoryId, onExpired, revision]);
  useEffect(() => () => request.current?.abort(), []);
  async function prepare() {
    setPending(true);
    setError(null);
    try {
      if (running) await cancelSearch(repositoryId, csrf);
      else await prepareSearch(repositoryId, mode, csrf);
      setRevision((v) => v + 1);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onExpired();
      else
        setError(
          reason instanceof ApiError
            ? reason.message
            : 'Unable to prepare search.',
        );
    } finally {
      setPending(false);
    }
  }
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setSearching(true);
    setError(null);
    setResult(null);
    try {
      const found = await querySearch(
        repositoryId,
        query,
        mode,
        csrf,
        controller.signal,
        rerank,
      );
      if (!controller.signal.aborted) setResult(found);
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && reason.status === 401) onExpired();
      else
        setError(
          reason instanceof ApiError
            ? reason.message
            : 'Search failed. Please try again.',
        );
    } finally {
      if (!controller.signal.aborted) setSearching(false);
    }
  }
  return (
    <section className="index-inspector" aria-label="Repository search">
      <div className="card-heading">
        <h3>Search the code</h3>
        <button
          disabled={pending || !state}
          onClick={() => {
            void prepare();
          }}
        >
          {pending
            ? 'Saving…'
            : running
              ? 'Cancel search preparation'
              : 'Prepare selected search'}
        </button>
      </div>
      <p>Inspect the evidence independently, then ask a question below.</p>
      {statusError && <p role="status">{statusError}</p>}
      {state?.latest && (
        <p role="status">
          Search preparation ({state.latest.mode}): {state.latest.status} ·{' '}
          {state.latest.documents_stored} chunks stored
        </p>
      )}
      {state?.latest?.error_message && (
        <p className="form-error">{state.latest.error_message}</p>
      )}
      {state?.latest?.mode === 'hybrid' && (
        <p className="field-help">
          Reserved embedding tokens: {state.latest.reserved_tokens}/
          {state.latest.token_budget} · conservative cost estimate $
          {(
            (state.latest.reserved_tokens * state.latest.price_per_million) /
            1000000
          ).toFixed(6)}
          . Reservations include uncertain requests.
        </p>
      )}
      <label htmlFor="search-mode">Search mode</label>
      <select
        id="search-mode"
        value={mode}
        disabled={pending || searching}
        onChange={(event) => {
          setMode(event.target.value === 'hybrid' ? 'hybrid' : 'keyword');
          setResult(null);
        }}
      >
        <option value="keyword">Keyword + symbols (free)</option>
        <option value="hybrid" disabled={!state?.enabled_semantic}>
          Hybrid + semantic (external embedding API)
        </option>
      </select>
      {mode === 'hybrid' && (
        <p className="field-help">
          Preparing sends repository source to OpenAI. Searching sends your
          query. Both can incur API charges.
        </p>
      )}
      <form
        onSubmit={(event) => {
          void search(event);
        }}
        className="search-form"
      >
        <label htmlFor="repository-query">Question or symbol</label>
        <input
          id="repository-query"
          value={query}
          maxLength={512}
          required
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Where is token validation implemented?"
        />
        <button type="submit" disabled={!ready || searching || !query.trim()}>
          {searching ? 'Searching…' : 'Search code'}
        </button>
      </form>
      {!ready && state && (
        <p>Prepare the selected search mode after building the source index.</p>
      )}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {searching && <p role="status">Retrieving source…</p>}
      <label>
        <input
          type="checkbox"
          checked={rerank}
          onChange={(e) => setRerank(e.target.checked)}
        />{' '}
        Rerank results using local relevance scoring (no model cost)
      </label>
      {result && <SearchResults result={result} />}
    </section>
  );
}
