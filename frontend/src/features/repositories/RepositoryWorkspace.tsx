import { useState } from 'react';
import { ApiError } from '../../lib/api/http';
import { createRepository, repositoryAction, type Repository } from './api';
import { useRepositories } from './useRepositories';
import { RepositoryForm } from './RepositoryForm';
import { RepositoryCard } from './RepositoryCard';
import { PullReviews } from '../reviews/PullReviews';
import { FileBrowser } from './FileBrowser';
interface Props {
  csrf: string;
  onExpired: () => void;
}
export function RepositoryWorkspace({ csrf, onExpired }: Props) {
  const { repositories, loading, error, refresh } = useRepositories(onExpired);
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [opened, setOpened] = useState<Repository | null>(null);
  async function perform(key: string, operation: () => Promise<void>) {
    setPending(key);
    setActionError(null);
    try {
      await operation();
      refresh();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onExpired();
      else
        setActionError(
          reason instanceof ApiError
            ? reason.message
            : 'The operation failed. Please try again.',
        );
    } finally {
      setPending(null);
    }
  }
  async function act(
    repo: Repository,
    action: 'retry' | 'refresh' | 'cancel' | 'delete',
  ) {
    if (
      action === 'delete' &&
      !window.confirm(
        `Remove ${repo.owner}/${repo.name} and its imported files?`,
      )
    )
      return;
    await perform(repo.id, async () => {
      await repositoryAction(repo.id, action, csrf);
      if (opened?.id === repo.id) setOpened(null);
    });
  }
  return (
    <section
      className="repositories-section"
      aria-labelledby="repositories-title"
    >
      <div className="card-heading">
        <div>
          <span className="eyebrow">SOURCE WORKSPACE</span>
          <h2 id="repositories-title">Your repositories</h2>
        </div>
        <button className="text-button" onClick={refresh}>
          Refresh
        </button>
      </div>
      <RepositoryForm
        pending={pending !== null}
        onSubmit={(url) => perform('create', () => createRepository(url, csrf))}
      />
      {(error || actionError) && (
        <p role="alert" className="form-error">
          {actionError ?? error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading repositories…</p>
      ) : repositories.length === 0 && !error ? (
        <div className="empty-state">
          <strong>No repositories connected yet.</strong>
          <p>Add a public GitHub URL above to begin.</p>
        </div>
      ) : (
        <div className="repository-list">
          {repositories.map((repo) => (
            <RepositoryCard
              key={repo.id}
              repository={repo}
              pending={pending !== null}
              onAction={(action) => {
                void act(repo, action);
              }}
              onOpen={() => setOpened(repo)}
            />
          ))}
        </div>
      )}
      {opened && (
        <PullReviews
          key={`reviews-${opened.id}`}
          repositoryId={opened.id}
          csrf={csrf}
        />
      )}
      {opened && (
        <FileBrowser
          key={opened.id}
          repository={opened}
          csrf={csrf}
          onClose={() => setOpened(null)}
          onExpired={onExpired}
        />
      )}
    </section>
  );
}
