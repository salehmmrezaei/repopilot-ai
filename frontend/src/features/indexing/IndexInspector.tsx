import { useState } from 'react';
import { ApiError } from '../../lib/api/http';
import { indexAction } from './api';
import { useIndex } from './useIndex';
import { IndexedFileDetails } from './IndexedFileDetails';
interface Props {
  repositoryId: string;
  fileId: string | null;
  csrf: string;
  onExpired: () => void;
}
export function IndexInspector({
  repositoryId,
  fileId,
  csrf,
  onExpired,
}: Props) {
  const { state, error, refresh } = useIndex(repositoryId, onExpired);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const running =
    state?.latest && ['queued', 'running'].includes(state.latest.status);
  async function act(action: 'start' | 'cancel') {
    setPending(true);
    setActionError(null);
    try {
      await indexAction(repositoryId, action, csrf);
      refresh();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onExpired();
      else
        setActionError(
          reason instanceof ApiError
            ? reason.message
            : 'Index action failed. Try again.',
        );
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="index-inspector" aria-label="Source index">
      <div className="card-heading">
        <h3>Source index</h3>
        <button
          disabled={pending || !state}
          onClick={() => {
            void act(running ? 'cancel' : 'start');
          }}
        >
          {pending
            ? 'Saving…'
            : running
              ? 'Cancel indexing'
              : state?.rebuild_available
                ? 'Rebuild index'
                : state?.latest?.status === 'completed'
                  ? 'Check index'
                  : 'Build index'}
        </button>
      </div>
      {(error || actionError) && (
        <p role="alert" className="form-error">
          {actionError ?? error}
        </p>
      )}
      {!state && !error && <p role="status">Loading index status…</p>}
      {state && !state.latest && (
        <p>
          Build a static index to inspect Python and TypeScript/TSX symbols and
          source chunks. No AI API calls are needed.
        </p>
      )}
      {state?.rebuild_available && (
        <p role="status">
          A newer parser is available. Rebuild the source index, then prepare
          search again. Saved results retain their original source snapshots.
        </p>
      )}
      <p className="field-help">
        Static symbols: Python, TypeScript and TSX. Other imported languages use
        text chunks. TypeScript indexing does not run type checking or install
        dependencies.
      </p>
      {state?.latest && (
        <p role="status">
          Index {state.latest.status} ·{' '}
          {state.latest.stage.replaceAll('_', ' ')}
        </p>
      )}
      {state?.latest?.error_message && (
        <p role="alert" className="form-error">
          {state.latest.error_message}
        </p>
      )}
      {state?.active && (
        <>
          <p>
            {state.active.files_stored} files · {state.active.symbol_count}{' '}
            symbols · {state.active.chunk_count} chunks
          </p>
          {state.latest?.id !== state.active.id && (
            <p>
              Showing the previous completed index while the latest attempt is
              unavailable.
            </p>
          )}
          {state.active.diagnostics.length > 0 && (
            <details>
              <summary>
                {state.active.diagnostics.length} parsing diagnostics
              </summary>
              <ul>
                {state.active.diagnostics.map((d) => (
                  <li key={d.file_id}>
                    <code>{d.path}</code>: {d.message}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {fileId ? (
            <IndexedFileDetails
              key={`${state.active.id}:${fileId}`}
              repositoryId={repositoryId}
              fileId={fileId}
              onExpired={onExpired}
            />
          ) : (
            <p>Select a source file above to inspect its symbols and chunks.</p>
          )}
        </>
      )}
    </section>
  );
}
