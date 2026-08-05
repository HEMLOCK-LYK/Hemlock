import type { ReactNode } from "react";

interface MethodBadgeProps {
  method: string;
}

export function MethodBadge({ method }: MethodBadgeProps) {
  const m = method.toUpperCase();
  const cls = ["GET", "POST", "PUT", "DELETE", "PATCH"].includes(m)
    ? m
    : "OTHER";
  return <span className={`method-badge ${cls}`}>{m}</span>;
}

interface StatusCodeProps {
  status: number | null;
}

export function StatusCode({ status }: StatusCodeProps) {
  if (status === null || status === undefined) {
    return <span className="status-code" style={{ color: "var(--text-muted)" }}>-</span>;
  }
  const cls = `s${Math.floor(status / 100)}`;
  return <span className={`status-code ${cls}`}>{status}</span>;
}

interface SourceBadgeProps {
  source: string;
}

export function SourceBadge({ source }: SourceBadgeProps) {
  return (
    <span className={`badge source-${source}`}>{source}</span>
  );
}

interface RiskBadgeProps {
  level: string;
}

export function RiskBadge({ level }: RiskBadgeProps) {
  return <span className={`risk-badge ${level}`}>{level}</span>;
}

interface EmptyStateProps {
  icon?: ReactNode;
  message: string;
}

export function EmptyState({ icon, message }: EmptyStateProps) {
  return (
    <div className="empty-state">
      {icon}
      <span>{message}</span>
    </div>
  );
}

interface SpinnerProps {
  size?: number;
}

export function Spinner({ size = 16 }: SpinnerProps) {
  return (
    <span
      className="spinner"
      style={{ width: size, height: size }}
    />
  );
}
