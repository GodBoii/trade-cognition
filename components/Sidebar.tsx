"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { api } from "@/lib/api/client";
import { Badge } from "@/components/ui";
import { useAsync } from "@/lib/useAsync";
import { useAuth } from "@/state/auth";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/trade", label: "New trade" },
  { href: "/trades", label: "Trades" },
  { href: "/rules", label: "Rules" },
  { href: "/journal", label: "Journal" },
  { href: "/accounts", label: "Accounts" },
] as const;

export function Sidebar({ minimal = false }: { minimal?: boolean }) {
  const { user, accounts, accountId, selectAccount, signOut } = useAuth();
  const pathname = usePathname();
  const health = useAsync(() => api.health(), []);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">Trade Cognition</span>
      </div>
      <div className="brand-sub">disciplined execution</div>

      {!minimal && (
        <nav className="nav" aria-label="Main">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={isActive(link.href) ? "nav-link active" : "nav-link"}
              aria-current={isActive(link.href) ? "page" : undefined}
            >
              {link.label}
            </Link>
          ))}
        </nav>
      )}

      {accounts.length > 1 && (
        <label className="field" style={{ marginBottom: 0 }}>
          <span>Active account</span>
          <select
            value={accountId ?? ""}
            onChange={(event) => selectAccount(Number(event.target.value))}
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.login} — {account.server}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="sidebar-footer">
        {health.data && (
          <div className="stack-sm">
            <div className="inline">
              <Badge tone={health.data.mt5_gateway === "real" ? "ok" : "warn"}>
                {health.data.mt5_gateway === "real" ? "live MT5" : "simulated MT5"}
              </Badge>
              <Badge tone={health.data.monitor_running ? "ok" : "danger"}>
                {health.data.monitor_running ? "monitor on" : "monitor off"}
              </Badge>
            </div>
            <span className="tiny">
              v{health.data.version} · {health.data.environment}
            </span>
            {health.data.mt5_gateway !== "real" && (
              <span className="tiny">
                Orders are simulated. Set <code>TC_MT5_GATEWAY=real</code> to trade a terminal.
              </span>
            )}
          </div>
        )}
        <div className="between">
          <span className="tiny">{user?.display_name || user?.email}</span>
          <button className="btn btn-sm" onClick={signOut}>
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
