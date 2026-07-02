import React from "react";

interface Props {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Error Boundary — catches rendering errors and shows a fallback UI
 * instead of crashing the entire app.
 *
 * Usage: wrap any component tree that might throw:
 *   <ErrorBoundary>
 *     <DashboardPage />
 *   </ErrorBoundary>
 */
export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("[ErrorBoundary]", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div
          style={{
            padding: "32px",
            textAlign: "center",
            color: "var(--muted, #8fa1ad)",
            background: "var(--surface, #0e151b)",
            borderRadius: "var(--radius-md, 8px)",
            margin: "16px",
          }}
        >
          <div style={{ fontSize: "15px", fontWeight: 600, marginBottom: 8, color: "var(--danger, #f97373)" }}>
            组件加载出错
          </div>
          <div style={{ fontSize: "13px", marginBottom: 16 }}>
            {this.state.error?.message || "未知错误"}
          </div>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              padding: "6px 16px",
              borderRadius: "var(--radius-sm, 6px)",
              border: "1px solid var(--line, #26323c)",
              background: "var(--surface-soft, #131d25)",
              color: "var(--ink, #eef6f8)",
              cursor: "pointer",
              fontSize: "13px",
            }}
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
