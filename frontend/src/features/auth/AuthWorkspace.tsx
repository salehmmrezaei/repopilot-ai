import { useCallback, useEffect, useState } from 'react';
import { RepositoryWorkspace } from '../repositories/RepositoryWorkspace';
import { SystemStatus } from '../system/SystemStatus';
import { AuthForm } from './AuthForm';
import { AuthError, getSession, logout } from './api';
import type { AuthSession } from './types';

type State =
  | { kind: 'loading' }
  | { kind: 'guest' }
  | { kind: 'authenticated'; session: AuthSession }
  | { kind: 'error'; message: string };
export function AuthWorkspace() {
  const [state, setState] = useState<State>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const expireSession = useCallback(() => setState({ kind: 'guest' }), []);
  const [pending, setPending] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    void getSession(controller.signal)
      .then(
        (session) => {
          if (active)
            setState(
              session ? { kind: 'authenticated', session } : { kind: 'guest' },
            );
        },
        (error: unknown) => {
          if (active)
            setState({
              kind: 'error',
              message:
                error instanceof Error
                  ? error.message
                  : 'Unable to check your session.',
            });
        },
      )
      .finally(() => clearTimeout(timeout));
    return () => {
      active = false;
      clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);
  useEffect(() => {
    if (state.kind !== 'authenticated') return;
    const remaining = Date.parse(state.session.expires_at) - Date.now();
    const timer = setTimeout(
      () => setState({ kind: 'guest' }),
      Math.max(0, remaining),
    );
    return () => clearTimeout(timer);
  }, [state]);
  async function signOut() {
    if (state.kind !== 'authenticated') return;
    setPending(true);
    setLogoutError(null);
    try {
      await logout(state.session.csrf_token);
      setState({ kind: 'guest' });
    } catch (error) {
      if (error instanceof AuthError && error.status === 401)
        setState({ kind: 'guest' });
      else
        setLogoutError(
          error instanceof Error
            ? error.message
            : 'Sign-out failed. Try again.',
        );
    } finally {
      setPending(false);
    }
  }
  if (state.kind === 'loading')
    return (
      <p role="status" className="session-notice">
        Checking your session…
      </p>
    );
  if (state.kind === 'error')
    return (
      <section className="status-card">
        <p role="alert">{state.message}</p>
        <button
          onClick={() => {
            setState({ kind: 'loading' });
            setAttempt((value) => value + 1);
          }}
        >
          Retry session check
        </button>
      </section>
    );
  if (state.kind === 'guest')
    return (
      <div className="grid">
        <AuthForm
          onAuthenticated={(session) =>
            setState({ kind: 'authenticated', session })
          }
        />
        <section className="next-card">
          <span className="eyebrow">START WITH CLARITY</span>
          <h2>A home for your codebase knowledge.</h2>
          <p>
            Your account is the first step. Connect public repositories after
            signing in to explore cited answers, investigations and patch
            proposals.
          </p>
          <p className="note">
            Keep this development instance private. Email verification and
            account recovery are not available yet.
          </p>
        </section>
      </div>
    );
  return (
    <>
      <div className="grid">
        <section className="status-card">
          <span className="eyebrow">YOUR WORKSPACE</span>
          <h2>Welcome, {state.session.user.name}.</h2>
          <p>{state.session.user.email}</p>

          {logoutError && (
            <p role="alert" className="form-error">
              {logoutError}
            </p>
          )}
          <button
            disabled={pending}
            onClick={() => {
              void signOut();
            }}
          >
            {pending ? 'Signing out…' : 'Sign out'}
          </button>
        </section>
        <SystemStatus />
      </div>
      <RepositoryWorkspace
        csrf={state.session.csrf_token}
        onExpired={expireSession}
      />
    </>
  );
}
