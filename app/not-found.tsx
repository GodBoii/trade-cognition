import Link from "next/link";

export default function NotFound() {
  return (
    <div className="auth-shell">
      <div className="auth-card">
        <h1>Not found</h1>
        <p className="auth-tagline">That page does not exist.</p>
        <Link className="btn btn-primary btn-block" href="/">
          Back to the dashboard
        </Link>
      </div>
    </div>
  );
}
