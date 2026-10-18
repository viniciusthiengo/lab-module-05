import { TeamApp } from "@/components/TeamApp";

export default function Home() {
  return (
    <main className="page">
      <header className="page-header">
        <h1>Research Team</h1>
        <p>
          Give the team a research task. The Supervisor decides who works on what: the Researcher
          finds and summarizes information, the Writer turns it into a polished result, the Reviewer
          and the Fact-checker check it. Watch every decision and every result as it happens.
        </p>
      </header>
      <TeamApp />
      <footer className="page-footer">
        Supervisor pattern · DELEGATE/TASK and FINAL protocol · Claude
      </footer>
    </main>
  );
}
