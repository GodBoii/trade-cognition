"use client";

import { useState } from "react";

import { api } from "@/lib/api/client";
import { Banner, Card, ErrorBanner, Field } from "@/components/ui";
import { useAuth } from "@/state/auth";
import { money } from "@/lib/format";

export default function ConnectAccountView() {
  const { accounts, refreshAccounts, selectAccount } = useAuth();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState("");
  const [label, setLabel] = useState("");
  const [terminalPath, setTerminalPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const state = await api.connectAccount({
        login: Number(login),
        password,
        server: server.trim(),
        label: label.trim(),
        terminal_path: terminalPath.trim(),
      });
      await refreshAccounts();
      selectAccount(state.account.id);
      setPassword("");
      setDone(
        `Connected ${state.snapshot.company || state.account.server} account ${
          state.account.login
        } - balance ${money(state.snapshot.balance, state.snapshot.currency)}.`,
      );
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async (id: number) => {
    setError(null);
    try {
      await api.disconnectAccount(id);
      await refreshAccounts();
    } catch (cause) {
      setError(cause);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>MetaTrader 5 connection</h1>
          <p>
            Trade Cognition talks to your MT5 account through a terminal running on the server. The
            credentials are verified before they are saved, and the password is encrypted at rest.
          </p>
        </div>
      </div>

      <div className="grid grid-2">
        <Card title="Connect an account">
          <ErrorBanner error={error} />
          {done && <Banner tone="ok">{done}</Banner>}

          <form onSubmit={submit}>
            <Field label="Account number (login)">
              <input
                type="number"
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                required
                min={1}
              />
            </Field>

            <Field
              label="Password"
              hint="The master password. An investor (read-only) password cannot place orders."
            >
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
                required
              />
            </Field>

            <Field label="Server" hint="Exactly as it appears in the terminal, e.g. Broker-Live02">
              <input
                type="text"
                value={server}
                onChange={(e) => setServer(e.target.value)}
                required
              />
            </Field>

            <Field label="Label" hint="Optional name for your own reference">
              <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>

            <Field
              label="Terminal path"
              hint="Optional. Full path to terminal64.exe when auto-detection fails."
            >
              <input
                type="text"
                value={terminalPath}
                onChange={(e) => setTerminalPath(e.target.value)}
                placeholder="C:\Program Files\MetaTrader 5\terminal64.exe"
              />
            </Field>

            <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
              {busy ? "Verifying with the terminal..." : "Verify and connect"}
            </button>
          </form>
        </Card>

        <div className="stack">
          <Card title="Connected accounts">
            {accounts.length === 0 ? (
              <p className="muted mb-0">Nothing connected yet.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Login</th>
                      <th>Server</th>
                      <th className="num">Balance</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {accounts.map((account) => (
                      <tr key={account.id}>
                        <td>
                          <span className="mono">{account.login}</span>
                          {account.is_default && <span className="faint tiny"> default</span>}
                          <div className="tiny faint">{account.label}</div>
                        </td>
                        <td className="small">{account.server}</td>
                        <td className="num">
                          {money(account.last_balance, account.currency)}
                        </td>
                        <td className="right">
                          <button
                            className="btn btn-sm"
                            onClick={() => void disconnect(account.id)}
                          >
                            Disconnect
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="Before you connect">
            <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
              <li>
                The MT5 terminal must be installed and running on the machine hosting the backend,
                with <strong>Algo Trading</strong> enabled.
              </li>
              <li>
                The vendor MT5 library drives one account at a time, so requests are serialised.
                Start with a demo account.
              </li>
              <li>
                Set <code>TC_MT5_GATEWAY=mock</code> to explore the platform with a simulated broker
                and no terminal at all.
              </li>
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
