import { useEffect, useState } from 'react';
import { z } from 'zod';
import { requestJSON, writeHeaders } from '../../lib/api/http';
const schema = z.object({
  enabled: z.boolean(),
  connected: z.boolean(),
  login: z.string().nullable(),
  private_access: z.boolean(),
});
export function GitHubConnection({ csrf = '' }: { csrf?: string }) {
  const [state, setState] = useState<z.infer<typeof schema> | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [privateAccess, setPrivateAccess] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void requestJSON('/auth/github/status', { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setState(schema.parse(value));
      })
      .catch(() => {
        /* Optional integration; password sign-in remains available. */
      });
    return () => controller.abort();
  }, []);
  async function connect() {
    setPending(true);
    setError('');
    try {
      const result = z.object({ url: z.string().url() }).parse(
        await requestJSON('/auth/github/start', {
          method: 'POST',
          headers: writeHeaders(csrf),
          body: JSON.stringify({ private_access: privateAccess }),
        }),
      );
      const url = new URL(result.url);
      if (
        url.origin !== 'https://github.com' ||
        url.pathname !== '/login/oauth/authorize'
      )
        throw new Error('Invalid authorization URL.');
      window.location.assign(result.url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'GitHub connection failed.');
      setPending(false);
    }
  }
  async function disconnect() {
    setPending(true);
    setError('');
    try {
      await requestJSON('/auth/github/connection', {
        method: 'DELETE',
        headers: writeHeaders(csrf),
      });
      setState((s) => s && { ...s, connected: false, private_access: false });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Disconnect failed.');
    } finally {
      setPending(false);
    }
  }
  if (!state?.enabled) return null;
  return (
    <section className="status-card" aria-label="GitHub connection">
      <h3>GitHub {state.connected ? `· ${state.login}` : 'access'}</h3>
      <label>
        <input
          type="checkbox"
          checked={privateAccess}
          onChange={(e) => setPrivateAccess(e.target.checked)}
        />{' '}
        Allow private repository access
      </label>
      {privateAccess && (
        <p>
          GitHub’s repo permission includes write access. RepoPilot uses it to
          read repositories and pull requests. Review the permissions on GitHub
          before authorizing.
        </p>
      )}
      <button disabled={pending} onClick={() => void connect()}>
        {state.connected
          ? 'Reconnect GitHub'
          : csrf
            ? 'Connect GitHub'
            : 'Sign in with GitHub'}
      </button>
      {state.connected && (
        <>
          <p>
            {state.private_access
              ? 'Private repository permission granted.'
              : 'Public repository access.'}
          </p>
          <button disabled={pending} onClick={() => void disconnect()}>
            Disconnect repository access
          </button>
          <p>
            Disconnect removes the stored token. Imported source remains until
            you remove its repository. You can also revoke this app in GitHub
            settings.
          </p>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
