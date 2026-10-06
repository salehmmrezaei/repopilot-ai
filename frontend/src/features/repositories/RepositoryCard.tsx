import type { Repository } from './api';
interface Props {
  repository: Repository;
  pending: boolean;
  onAction: (action: 'retry' | 'refresh' | 'cancel' | 'delete') => void;
  onOpen: () => void;
}
export function RepositoryCard({
  repository: repo,
  pending,
  onAction,
  onOpen,
}: Props) {
  const active = ['queued', 'running'].includes(repo.job.status);
  return (
    <article className="repository-card">
      <div className="card-heading">
        <h3>
          {repo.owner}/{repo.name}
        </h3>
        <span
          className={`badge ${repo.job.status === 'completed' ? 'ready' : repo.job.status === 'failed' ? 'error' : ''}`}
        >
          {repo.job.status === 'completed' ? 'Imported' : repo.job.status}
        </span>
      </div>
      <p className="repository-progress" role="status">
        {active
          ? `Import stage: ${repo.job.stage.replaceAll('_', ' ')} · attempt ${repo.job.attempts}/3`
          : `${repo.job.files_stored} files stored · ${repo.job.files_skipped} skipped`}
      </p>
      {repo.last_commit_sha && (
        <p className="commit-label">
          {repo.default_branch} · {repo.last_commit_sha.slice(0, 12)}
        </p>
      )}
      {repo.job.error_message && (
        <p className="form-error">{repo.job.error_message}</p>
      )}
      <div className="repository-actions">
        {repo.last_commit_sha && <button onClick={onOpen}>Browse files</button>}
        {repo.job.status === 'completed' && (
          <button disabled={pending} onClick={() => onAction('refresh')}>
            Refresh from GitHub
          </button>
        )}
        {active && (
          <button disabled={pending} onClick={() => onAction('cancel')}>
            Cancel import
          </button>
        )}
        {['failed', 'cancelled'].includes(repo.job.status) && (
          <button disabled={pending} onClick={() => onAction('retry')}>
            Retry import
          </button>
        )}
        <button
          className="text-button"
          disabled={pending}
          onClick={() => onAction('delete')}
        >
          Remove
        </button>
      </div>
    </article>
  );
}
