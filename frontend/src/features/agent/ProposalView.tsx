import type { AgentRun } from './api';

type Proposal = NonNullable<NonNullable<AgentRun['result']>['proposal']>;
export function ProposalView({
  runId,
  commit,
  proposal,
}: {
  runId: string;
  commit: string;
  proposal: Proposal;
}) {
  return (
    <section aria-label="Patch proposal">
      <h5>Implementation plan</h5>
      <p>
        <strong>Source snapshot checked · Tests not run · Not applied</strong>
      </p>
      <p>
        Review against commit {commit}. Imported snapshots may omit files;
        new-file absence must also be checked in a complete checkout. Source
        matching does not prove correctness.
      </p>
      <ol>
        {proposal.implementation_plan.map((step, i) => (
          <li key={i}>{step}</li>
        ))}
      </ol>
      <h5>Risks and assumptions</h5>
      <ul>
        {proposal.risks.map((risk, i) => (
          <li key={i}>{risk}</li>
        ))}
      </ul>
      <h5>Suggested checks — not executed</h5>
      <ul>
        {proposal.test_plan.map((test, i) => (
          <li key={i}>{test}</li>
        ))}
      </ul>
      <h5>Changed files</h5>
      <ul>
        {proposal.files.map((file) => (
          <li key={file.path}>
            {file.operation}: {file.path}
            <details>
              <summary>Content fingerprints</summary>
              <p>
                Before: {file.before_sha256 ?? 'Absent from imported snapshot'}
              </p>
              <p>After: {file.after_sha256 ?? 'Proposed deletion'}</p>
            </details>
          </li>
        ))}
      </ul>
      <h5>Unified diff</h5>
      <pre tabIndex={0} aria-label="Proposed diff">
        <code>{proposal.diff}</code>
      </pre>
      <p className="field-help">Patch SHA-256: {proposal.diff_sha256}</p>
      <a
        href={`/api/agent-runs/${encodeURIComponent(runId)}/patch`}
        download={`proposal-${runId}.patch`}
      >
        Download patch for review
      </a>
    </section>
  );
}
