"use client";

import { useState } from "react";

import { Badge, Banner, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import { money } from "@/lib/format";
import type { WorkerAgent } from "@/lib/supabase/types";
import { useTrading } from "@/state/trading";

export default function ConnectAccountView() {
  const {
    connections,
    workers,
    loading,
    error: loadError,
    connectionId,
    selectConnection,
    refresh,
    createPairing,
    setEnabled,
    rotateWorkerToken,
    revokeWorker,
  } = useTrading();
  const [workerName, setWorkerName] = useState("Docker MT5 worker");
  const [connectionLabel, setConnectionLabel] = useState("My MT5 account");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [workerToken, setWorkerToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const setup = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("create");
    setError(null);
    setWorkerToken(null);
    setCopied(false);
    try {
      const credential = await createPairing(workerName, connectionLabel);
      setWorkerToken(credential.workerToken);
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(null);
    }
  };

  const copyToken = async () => {
    if (!workerToken) return;
    try {
      await navigator.clipboard.writeText(workerToken);
      setCopied(true);
    } catch (cause) {
      setError(cause);
    }
  };

  const rotate = async (worker: WorkerAgent) => {
    setBusy(`rotate:${worker.id}`);
    setError(null);
    setWorkerToken(null);
    setCopied(false);
    try {
      setWorkerToken(await rotateWorkerToken(worker.id));
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(null);
    }
  };

  const revoke = async (worker: WorkerAgent) => {
    if (!window.confirm(`Revoke ${worker.name}? Its local token will stop working immediately.`)) {
      return;
    }
    setBusy(`revoke:${worker.id}`);
    setError(null);
    try {
      await revokeWorker(worker.id);
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(null);
    }
  };

  const toggle = async (id: string, enabled: boolean) => {
    setBusy(`connection:${id}`);
    setError(null);
    try {
      await setEnabled(id, enabled);
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>MetaTrader 5 connection</h1>
          <p>
            Pair this website with the Trade Cognition worker. The browser queues instructions in
            Supabase; the trusted local runtime validates and executes supported commands.
          </p>
        </div>
        <button className="btn btn-sm" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh status"}
        </button>
      </div>

      <ErrorBanner error={loadError} />
      <ErrorBanner error={error} />

      {workerToken && (
        <Banner tone="warn" title="Copy this worker token now — it is shown only once">
          <p className="small">
            Put it in the local Docker environment as <code>TC_WORKER_TOKEN</code>. Do not
            add it to Vercel, Git, screenshots, or browser storage.
          </p>
          <div className="inline">
            <code style={{ overflowWrap: "anywhere" }}>{workerToken}</code>
            <button className="btn btn-sm" type="button" onClick={() => void copyToken()}>
              {copied ? "Copied" : "Copy token"}
            </button>
          </div>
        </Banner>
      )}

      <div className="grid grid-2 mt">
        <Card title="Create a local worker pairing">
          <form onSubmit={setup}>
            <Field label="Worker name" hint="A label for the computer running Docker Desktop.">
              <input
                type="text"
                value={workerName}
                onChange={(event) => setWorkerName(event.target.value)}
                minLength={1}
                maxLength={120}
                required
              />
            </Field>
            <Field label="MT5 account label" hint="The worker fills in the broker and login later.">
              <input
                type="text"
                value={connectionLabel}
                onChange={(event) => setConnectionLabel(event.target.value)}
                minLength={1}
                maxLength={120}
                required
              />
            </Field>
            <button className="btn btn-primary btn-block" type="submit" disabled={busy !== null}>
              {busy === "create" ? "Creating secure pairing..." : "Create worker and connection"}
            </button>
          </form>
          <p className="tiny faint mb-0 mt">
            This creates only a scoped queue token and an empty connection record. It cannot read
            or control another user&apos;s data.
          </p>
        </Card>

        <Card title="Credentials stay on your computer">
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
            <li>Sign in to your broker inside the local MetaTrader 5 terminal.</li>
            <li>The website never asks for or stores your MT5 password or terminal path.</li>
            <li>Supabase stores non-secret account status, trading rules, queued work, and logs.</li>
            <li>Linux Docker currently runs the mock broker; real MT5 needs the Windows runtime.</li>
            <li>Keep the chosen worker, MT5, and Algo Trading running while a trade is managed.</li>
            <li>Hard broker SL/TP remain active if your local worker temporarily goes offline.</li>
          </ul>
        </Card>
      </div>

      <div className="mt">
        <Card title="MT5 connections" actions={<Badge tone="muted">{connections.length}</Badge>}>
          {loading && connections.length === 0 ? (
            <Spinner label="loading connections" />
          ) : connections.length === 0 ? (
            <p className="muted mb-0">No connection yet. Create the first pairing above.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Worker</th>
                    <th>Status</th>
                    <th className="num">Balance</th>
                    <th>Last update</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {connections.map((item) => {
                    const worker = workers.find((entry) => entry.id === item.worker_id);
                    const selected = item.id === connectionId;
                    return (
                      <tr key={item.id}>
                        <td>
                          <span className="strong">{item.label}</span>
                          <div className="tiny faint mono">
                            {item.mt5_login ?? "awaiting MT5"}
                            {item.server ? ` · ${item.server}` : ""}
                          </div>
                        </td>
                        <td className="small">{worker?.name ?? "-"}</td>
                        <td>
                          <Badge
                            tone={
                              item.status === "online"
                                ? "ok"
                                : item.status === "error"
                                  ? "danger"
                                  : "warn"
                            }
                          >
                            {item.status}
                          </Badge>
                          {!item.is_enabled && <div className="tiny faint">paused</div>}
                          {item.last_error && <div className="tiny neg">{item.last_error}</div>}
                        </td>
                        <td className="num">
                          {item.last_balance === null
                            ? "-"
                            : money(item.last_balance, item.currency || "USD")}
                        </td>
                        <td className="small nowrap">
                          {item.last_seen_at ? new Date(item.last_seen_at).toLocaleString() : "never"}
                        </td>
                        <td className="right">
                          <div className="btn-group">
                            {!selected && (
                              <button className="btn btn-sm" onClick={() => selectConnection(item.id)}>
                                Select
                              </button>
                            )}
                            <button
                              className="btn btn-sm"
                              disabled={busy !== null}
                              onClick={() => void toggle(item.id, !item.is_enabled)}
                            >
                              {item.is_enabled ? "Pause" : "Enable"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <div className="mt">
        <Card title="Local workers" actions={<Badge tone="muted">{workers.length}</Badge>}>
          {workers.length === 0 ? (
            <p className="muted mb-0">No worker registered.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Last seen</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {workers.map((worker) => (
                    <tr key={worker.id}>
                      <td className="strong">{worker.name}</td>
                      <td className="small nowrap">
                        {worker.last_seen_at ? new Date(worker.last_seen_at).toLocaleString() : "never"}
                      </td>
                      <td>
                        <Badge
                          tone={worker.revoked_at ? "danger" : worker.last_seen_at ? "ok" : "warn"}
                        >
                          {worker.revoked_at
                            ? "revoked"
                            : worker.last_seen_at
                              ? "registered"
                              : "awaiting start"}
                        </Badge>
                      </td>
                      <td className="right">
                        {!worker.revoked_at && (
                          <div className="btn-group">
                            <button
                              className="btn btn-sm"
                              disabled={busy !== null}
                              onClick={() => void rotate(worker)}
                            >
                              Rotate token
                            </button>
                            <button
                              className="btn btn-sm btn-danger"
                              disabled={busy !== null}
                              onClick={() => void revoke(worker)}
                            >
                              Revoke
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
