"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge, type BadgeTone } from "@/components/ui";
import { useAuth } from "@/state/auth";
import { useTrading, type TradingStatus } from "@/state/trading";

const LINKS = [
  { href: "/", label: "Dashboard", icon: "◆" },
  { href: "/trade", label: "New trade", icon: "⊕" },
  { href: "/trades", label: "Trades", icon: "⇄" },
  { href: "/rules", label: "Rules", icon: "◈" },
  { href: "/journal", label: "Journal", icon: "◉" },
  { href: "/accounts", label: "Accounts", icon: "⊙" },
] as const;

const STATUS_LABEL: Record<TradingStatus, string> = {
  idle: "worker unknown",
  loading: "checking worker",
  error: "status unavailable",
  unpaired: "MT5 not paired",
  pending: "pairing pending",
  online: "worker online",
  stale: "worker stale",
  offline: "worker offline",
};

function statusTone(status: TradingStatus): BadgeTone {
  if (status === "online") return "ok";
  if (status === "error") return "danger";
  if (status === "loading" || status === "idle") return "muted";
  return "warn";
}

export function Sidebar({ minimal = false }: { minimal?: boolean }) {
  const { user, signOut } = useAuth();
  const { connections, connectionId, connection, status, selectConnection } = useTrading();
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <aside className="sidebar">
      <div>
        <div className="brand">
          <span className="brand-mark">Trade Cognition</span>
        </div>
        <div className="brand-sub">disciplined execution</div>
      </div>

      {!minimal && (
        <nav className="nav" aria-label="Main">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={isActive(link.href) ? "nav-link active" : "nav-link"}
              aria-current={isActive(link.href) ? "page" : undefined}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ opacity: 0.5, fontSize: 12 }}>{link.icon}</span>
                {link.label}
              </span>
            </Link>
          ))}
        </nav>
      )}

      {connections.length > 1 && (
        <label className="field" style={{ marginBottom: 0 }}>
          <span>Active account</span>
          <select
            value={connectionId ?? ""}
            onChange={(event) => selectConnection(event.target.value)}
          >
            {connections.map((item) => (
              <option key={item.id} value={item.id}>
                {item.mt5_login ?? "Pending"} — {item.server || item.label}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="sidebar-footer">
        <div className="stack-sm">
          <div className="inline">
            <Badge tone={statusTone(status)}>{STATUS_LABEL[status]}</Badge>
            {connection && (
              <Badge tone={connection.is_enabled ? "info" : "muted"}>
                {connection.is_enabled ? "automation enabled" : "automation paused"}
              </Badge>
            )}
          </div>
          <span className="tiny">
            {connection?.last_seen_at
              ? `Last MT5 update ${new Date(connection.last_seen_at).toLocaleString()}`
              : "The website remains available while your local worker is offline."}
          </span>
        </div>
        <div className="between">
          <span className="tiny">{user?.displayName || user?.email}</span>
          <button className="btn btn-sm" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
