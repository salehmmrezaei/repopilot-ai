import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { RunComposer } from '../runs/RunComposer';
import { ApiError } from '../../lib/api/http';
import { AnswerPanel } from '../answers/AnswerPanel';
import {
  createConversation,
  deleteConversation,
  listConversations,
  type Conversation,
} from './api';
import { ConversationTranscript } from './ConversationTranscript';
import { AgentPanel } from '../agent/AgentPanel';
interface Props {
  repositoryId: string;
  csrf: string;
  onExpired: () => void;
}
export function ConversationWorkspace({
  repositoryId,
  csrf,
  onExpired,
}: Props) {
  const id = useId();
  const [items, setItems] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [title, setTitle] = useState('');
  const [revision, setRevision] = useState(0);
  const [historyRevision, setHistoryRevision] = useState(0);
  const completed = useCallback(() => {
    setHistoryRevision((v) => v + 1);
    setRevision((v) => v + 1);
  }, []);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [answerBusy, setAnswerBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void listConversations(repositoryId, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) {
          setItems(next);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 401) onExpired();
        else
          setError('Unable to load conversations. Refresh the list to retry.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [repositoryId, revision, onExpired]);
  function failure(reason: unknown) {
    if (reason instanceof ApiError && reason.status === 401) onExpired();
    else
      setError(
        reason instanceof ApiError
          ? reason.message
          : 'Unable to update conversations. Refresh to check before retrying.',
      );
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await createConversation(
        repositoryId,
        title.trim(),
        csrf,
      );
      setSelected(created);
      setConfirmDelete(false);
      setTitle('');
      setRevision((v) => v + 1);
    } catch (reason) {
      failure(reason);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await deleteConversation(selected.id, csrf);
      setSelected(null);
      setConfirmDelete(false);
      setRevision((v) => v + 1);
    } catch (reason) {
      failure(reason);
    } finally {
      setBusy(false);
    }
  }
  const disabled = busy || answerBusy;
  return (
    <section
      className="index-inspector conversation-workspace"
      aria-label="Conversations"
    >
      <h3>Conversations</h3>
      <p>
        Save questions, answers and cited code in your account. Deleting a
        conversation removes its saved messages.
      </p>
      <form
        className="conversation-create"
        onSubmit={(event) => {
          void create(event);
        }}
      >
        <label htmlFor={`${id}-title`}>Conversation title</label>
        <input
          id={`${id}-title`}
          maxLength={100}
          required
          value={title}
          disabled={disabled}
          onChange={(event) => setTitle(event.target.value)}
        />
        <button disabled={disabled || !title.trim()} type="submit">
          Create conversation
        </button>
      </form>
      <button
        className="text-button"
        disabled={disabled || loading}
        onClick={() => {
          setLoading(true);
          setRevision((v) => v + 1);
        }}
      >
        Refresh conversations
      </button>
      {loading && <p role="status">Loading conversations…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p>No conversations yet. Create one to keep your answers.</p>
      )}
      <nav className="conversation-list" aria-label="Choose conversation">
        <button
          disabled={disabled}
          aria-pressed={!selected}
          onClick={() => {
            setSelected(null);
            setConfirmDelete(false);
          }}
        >
          Temporary question
        </button>
        {items.map((item) => (
          <button
            key={item.id}
            disabled={disabled}
            aria-pressed={selected?.id === item.id}
            onClick={() => {
              setSelected(item);
              setConfirmDelete(false);
            }}
          >
            {item.title} · {item.message_count / 2} saved answers
          </button>
        ))}
      </nav>
      {selected && (
        <>
          <div className="card-heading">
            <h4>{selected.title}</h4>
            <button
              className="text-button"
              disabled={disabled}
              onClick={() => setConfirmDelete(true)}
            >
              Delete conversation
            </button>
          </div>
          {confirmDelete && (
            <div role="group" aria-label="Confirm conversation deletion">
              <p>
                Delete this conversation and all its saved questions, answers
                and sources? This cannot be undone.
              </p>
              <button
                disabled={disabled}
                onClick={() => {
                  void remove();
                }}
              >
                Confirm deletion
              </button>
              <button
                disabled={disabled}
                onClick={() => setConfirmDelete(false)}
              >
                Keep conversation
              </button>
            </div>
          )}
          <ConversationTranscript
            key={`${selected.id}-${historyRevision}`}
            conversationId={selected.id}
            onExpired={onExpired}
          />
        </>
      )}
      {selected ? (
        <RunComposer
          key={selected.id}
          conversationId={selected.id}
          repositoryId={repositoryId}
          csrf={csrf}
          onExpired={onExpired}
          onCompleted={completed}
        />
      ) : (
        <AnswerPanel
          key="temporary"
          repositoryId={repositoryId}
          csrf={csrf}
          onExpired={onExpired}
          onBusy={setAnswerBusy}
        />
      )}
      {selected && (
        <AgentPanel
          key={`agent-${selected.id}`}
          conversationId={selected.id}
          csrf={csrf}
          onExpired={onExpired}
        />
      )}
    </section>
  );
}
