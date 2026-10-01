import { AuthWorkspace } from '../features/auth/AuthWorkspace';

export function App() {
  return (
    <div className="workspace">
      <header>
        <a className="brand" href="/" aria-label="RepoPilot AI home">
          <span className="brand-mark" aria-hidden="true">
            rp
          </span>
          RepoPilot <span className="brand-ai">AI</span>
        </a>
        <span className="milestone">10 / Sandbox validation</span>
      </header>
      <main>
        <div className="intro">
          <span className="eyebrow">CODEBASE INTELLIGENCE</span>
          <h1>
            Understand the code.
            <br />
            <span>Build with confidence.</span>
          </h1>
          <p>
            A workspace for grounded repository exploration. We’re starting with
            a secure workspace for everything that comes next.
          </p>
        </div>
        <AuthWorkspace />
      </main>
      <footer>
        RepoPilot AI <span>Milestone 7C2B · React / FastAPI / PostgreSQL</span>
      </footer>
    </div>
  );
}
