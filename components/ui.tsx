"use client";

/** Small presentational primitives shared across pages. */

import type { ReactNode } from "react";

export function Card({
  title,
  hint,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={className ? `card ${className}` : "card"}>
      {(title || actions) && (
        <header className="card-head">
          {typeof title === "string" ? <h2>{title}</h2> : title}
          {actions}
        </header>
      )}
      {hint && <p className="card-hint">{hint}</p>}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: "pos" | "neg" | "muted";
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={tone ? `stat-value ${tone}` : "stat-value"}>{value}</div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

export type BadgeTone = "ok" | "warn" | "danger" | "info" | "muted";

export function Badge({ tone = "muted", children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Banner({
  tone = "info",
  title,
  children,
}: {
  tone?: "error" | "warn" | "ok" | "info";
  title?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={`banner banner-${tone}`} role={tone === "error" ? "alert" : undefined}>
      {title && <div className="banner-title">{title}</div>}
      {children}
    </div>
  );
}

/** Renders errors from Supabase, browser APIs, or the local UI uniformly. */
export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  return <Banner tone="error">{error instanceof Error ? error.message : String(error)}</Banner>;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline">
      <span className="spinner" aria-hidden="true" />
      {label && <span className="muted small">{label}</span>}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </label>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone: BadgeTone =
    status === "open" || status === "scaling"
      ? "info"
      : status === "closed"
        ? "muted"
        : status === "error" || status === "rejected"
          ? "danger"
          : "warn";
  return <Badge tone={tone}>{status}</Badge>;
}

export function SideBadge({ side }: { side: "buy" | "sell" }) {
  return <Badge tone={side === "buy" ? "ok" : "danger"}>{side === "buy" ? "LONG" : "SHORT"}</Badge>;
}
