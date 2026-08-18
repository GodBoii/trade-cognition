"use client";

/** Small presentational primitives shared across pages. */

import { type ReactNode, useEffect, useRef, useState } from "react";

/* ================================================================
   Card — glassmorphism panel with stagger entrance
   ================================================================ */
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

/* ================================================================
   Stat — metric display with digit pop-in on mount / update
   ================================================================ */
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
  const valueStr = typeof value === "string" || typeof value === "number" ? String(value) : null;

  return (
    <div className="stat stagger-enter">
      <div className="stat-label">{label}</div>
      <div className={tone ? `stat-value ${tone}` : "stat-value"}>
        {valueStr ? <AnimatedValue text={valueStr} /> : value}
      </div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

/** Wraps a string value with the number pop-in animation (#02). */
function AnimatedValue({ text }: { text: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [prevText, setPrevText] = useState<string | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (prevText !== text) {
      el.classList.remove("is-animating");
      // eslint-disable-next-line @typescript-eslint/no-unused-expressions
      void el.offsetHeight; // force reflow for replay
      el.classList.add("is-animating");
      setPrevText(text);
    }
  }, [text, prevText]);

  const chars = text.split("");
  return (
    <span className="t-digit-group is-animating" ref={ref}>
      {chars.map((ch, i) => {
        const stagger =
          i === chars.length - 2 ? "1" : i === chars.length - 1 ? "2" : undefined;
        return (
          <span key={`${i}-${ch}`} className="t-digit" data-stagger={stagger}>
            {ch}
          </span>
        );
      })}
    </span>
  );
}

/* ================================================================
   Badge
   ================================================================ */
export type BadgeTone = "ok" | "warn" | "danger" | "info" | "muted";

export function Badge({ tone = "muted", children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

/* ================================================================
   Banner — toast-style entrance animation (#22)
   ================================================================ */
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

/* ================================================================
   Spinner — ring + shimmer text label (#15)
   ================================================================ */
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline">
      <span className="spinner" aria-hidden="true" />
      {label && (
        <span className="t-shimmer small" data-text={label}>
          {label}
        </span>
      )}
    </span>
  );
}

/* ================================================================
   Empty state
   ================================================================ */
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/* ================================================================
   Field
   ================================================================ */
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

/* ================================================================
   Status / Side badges
   ================================================================ */
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

/* ================================================================
   PageHead — staggered texts-reveal heading (#18)
   ================================================================ */
export function PageHead({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Trigger the stagger entrance after mount
    requestAnimationFrame(() => el.classList.add("is-shown"));
  }, []);

  return (
    <div className="page-head">
      <div className="t-stagger" ref={ref}>
        <h1 className="t-stagger-line t-stagger-line--1">{title}</h1>
        {subtitle && <p className="t-stagger-line t-stagger-line--2">{subtitle}</p>}
      </div>
      {actions && <div className="inline">{actions}</div>}
    </div>
  );
}
