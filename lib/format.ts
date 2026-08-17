/** Presentation helpers. Formatting lives here so tables stay readable. */

export function money(value: number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const formatted = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const sign = value < 0 ? "-" : "";
  return `${sign}${formatted} ${currency}`;
}

export function signedMoney(value: number, currency = "USD"): string {
  const prefix = value > 0 ? "+" : "";
  return prefix + money(value, currency);
}

export function price(value: number | null | undefined, digits = 5): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return value.toFixed(digits);
}

export function lots(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toFixed(2);
}

export function percent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${value.toFixed(digits)}%`;
}

export function points(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${Math.round(value).toLocaleString()} pts`;
}

export function ratio(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${value.toFixed(2)}R`;
}

export function time(iso: string | null | undefined): string {
  if (!iso) return "-";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "-"
    : date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString();
}

export function sign(value: number): "pos" | "neg" | "muted" {
  if (value > 0) return "pos";
  if (value < 0) return "neg";
  return "muted";
}

/** Human label for a rule code, used when a message is too long for a table. */
export function humanise(text: string): string {
  return text
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/^\w/, (c) => c.toUpperCase());
}


/**
 * Infer a sensible number of decimals when the symbol specification is not to
 * hand (position and trade payloads carry prices but not `digits`).
 *
 * Deliberately coarse: it is only for display. Anything that feeds an order goes
 * through the backend, which uses the broker's real `digits` and tick size.
 */
export function guessDigits(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 5;
  if (value >= 1000) return 2;
  if (value >= 100) return 3;
  return 5;
}
