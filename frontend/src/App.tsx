import React from "react";

interface Health {
  status?: string;
}
export function App(): JSX.Element {
  const [status, setStatus] = React.useState<string>("…");

  React.useEffect(() => {
    fetch("/api/healthz")
      .then((r) => r.json() as Promise<Health>)
      .then((j) => setStatus(j.status ?? "unknown"))
      .catch(() => setStatus("error"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>Frontend Scaffold</h1>
      <p>
        API status: <b data-testid="status">{status}</b>
      </p>
    </main>
  );
}
