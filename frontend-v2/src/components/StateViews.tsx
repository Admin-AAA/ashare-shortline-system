import type { ReactNode } from "react";
import { AlertCircle, RefreshCw } from "lucide-react";

interface PageSkeletonProps {
  title: string;
  hint?: string;
  rowCount?: number;
}

/** 整页加载骨架 */
export function PageSkeleton({ title, hint, rowCount = 3 }: PageSkeletonProps) {
  return (
    <div className="panel" style={{ padding: "var(--space-6)" }}>
      <div className="panel__kicker">加载中</div>
      <h2 style={{ margin: 0, marginBottom: "var(--space-2)", color: "var(--ink)" }}>{title}</h2>
      {hint && <p className="text-muted" style={{ margin: 0, marginBottom: "var(--space-5)" }}>{hint}</p>}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        {Array.from({ length: rowCount }).map((_, i) => (
          <div
            key={i}
            className="skeleton"
            style={{ height: i === 0 ? 32 : 20, width: `${100 - i * 8}%` }}
          />
        ))}
      </div>
    </div>
  );
}

interface CardSkeletonProps {
  count?: number;
}

/** 卡片内骨架 */
export function CardSkeleton({ count = 3 }: CardSkeletonProps) {
  return (
    <div style={{ display: "grid", gap: "var(--space-3)" }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 48 }} />
      ))}
    </div>
  );
}

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  reason?: string;
  action?: ReactNode;
  secondaryAction?: ReactNode;
}

/** 统一空状态 */
export function EmptyState({ icon, title, reason, action, secondaryAction }: EmptyStateProps) {
  return (
    <div className="state-block">
      {icon ?? <AlertCircle size={32} style={{ color: "var(--muted)" }} />}
      <div className="state-block__title">{title}</div>
      {reason && <div className="text-muted" style={{ maxWidth: 460 }}>{reason}</div>}
      {(action || secondaryAction) && (
        <div className="state-block__actions">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}

interface ErrorPageProps {
  title: string;
  message: string;
  onRetry?: () => void;
  secondary?: ReactNode;
}

/** 整页错误 */
export function ErrorPage({ title, message, onRetry, secondary }: ErrorPageProps) {
  return (
    <div className="state-block">
      <AlertCircle size={36} style={{ color: "var(--danger)" }} />
      <div className="state-block__title">{title}</div>
      <div className="text-muted" style={{ maxWidth: 480, fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)" }}>
        {message}
      </div>
      <div className="state-block__actions">
        {onRetry && (
          <button className="btn btn--primary" onClick={onRetry}>
            <RefreshCw size={14} />
            重试
          </button>
        )}
        {secondary}
      </div>
    </div>
  );
}
