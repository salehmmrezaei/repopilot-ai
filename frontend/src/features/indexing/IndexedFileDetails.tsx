import { useEffect, useState } from 'react';
import { ApiError } from '../../lib/api/http';
import { getIndexedFile, type IndexedFile } from './api';
interface Props {
  repositoryId: string;
  fileId: string;
  onExpired: () => void;
}
export function IndexedFileDetails({ repositoryId, fileId, onExpired }: Props) {
  const [page, setPage] = useState<IndexedFile | null>(null);
  const [symbols, setSymbols] = useState(0);
  const [chunks, setChunks] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void getIndexedFile(
      repositoryId,
      fileId,
      symbols,
      chunks,
      controller.signal,
    )
      .then((next) => {
        if (active) setPage(next);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 401) onExpired();
        else setError('Unable to load indexed source.');
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [repositoryId, fileId, symbols, chunks, revision, onExpired]);
  function resetPage() {
    setPage(null);
    setError(null);
  }
  if (error)
    return (
      <div role="alert">
        <p>{error}</p>
        <button
          onClick={() => {
            resetPage();
            setRevision((v) => v + 1);
          }}
        >
          Retry indexed source
        </button>
      </div>
    );
  if (!page) return <p role="status">Loading symbols and chunks…</p>;
  return (
    <div className="indexed-file">
      <h4>Index: {page.path}</h4>
      <p className="field-help">
        {page.language === 'typescript' &&
        page.path.toLowerCase().endsWith('.tsx')
          ? 'TypeScript / TSX'
          : page.language}{' '}
        · Commit {page.commit_sha} · source lines are one-based; character
        offsets use an exclusive end.
      </p>
      <h5>Symbols</h5>
      {page.symbols.length === 0 ? (
        <p>
          No extracted symbols on this page. Plain-text files and syntax
          fallbacks still have searchable source chunks.
        </p>
      ) : (
        <ul className="symbol-list">
          {page.symbols.map((symbol) => (
            <li key={symbol.id}>
              <code>{symbol.qualified_name}</code>
              <span>
                {symbol.kind} · L{symbol.start_line}–{symbol.end_line}
              </span>
              {(symbol.signature || symbol.docstring) && (
                <details>
                  <summary>Declaration details</summary>
                  {symbol.signature && (
                    <pre>
                      <code>{symbol.signature}</code>
                    </pre>
                  )}
                  {symbol.docstring && <pre>{symbol.docstring}</pre>}
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="repository-actions">
        {symbols > 0 && (
          <button
            onClick={() => {
              resetPage();
              setSymbols(Math.max(0, symbols - 50));
            }}
          >
            Previous symbols
          </button>
        )}
        {page.next_symbol_offset !== null && (
          <button
            onClick={() => {
              resetPage();
              setSymbols(page.next_symbol_offset ?? 0);
            }}
          >
            Next symbols
          </button>
        )}
      </div>
      <h5>Source chunks</h5>
      {page.chunks.length === 0 && (
        <p>This file is empty; no chunks were created.</p>
      )}
      {page.chunks.map((chunk) => (
        <details key={chunk.id} className="chunk-detail">
          <summary>
            Chunk {chunk.ordinal + 1} · {chunk.kind} · L{chunk.start_line}–
            {chunk.end_line}
          </summary>
          <p className="field-help">
            Characters [{chunk.start_offset}, {chunk.end_offset})
          </p>
          <pre tabIndex={0} aria-label={`Chunk ${chunk.ordinal + 1} source`}>
            {chunk.content}
          </pre>
        </details>
      ))}
      <div className="repository-actions">
        {chunks > 0 && (
          <button
            onClick={() => {
              resetPage();
              setChunks(Math.max(0, chunks - 20));
            }}
          >
            Previous chunks
          </button>
        )}
        {page.next_chunk_offset !== null && (
          <button
            onClick={() => {
              resetPage();
              setChunks(page.next_chunk_offset ?? 0);
            }}
          >
            Next chunks
          </button>
        )}
      </div>
    </div>
  );
}
