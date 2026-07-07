import { useState, type CSSProperties, type ReactNode } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

interface CollapsiblePanelProps {
  /** 卡片标题（可含图标，支持 ReactNode） */
  title: ReactNode;
  /** 标题上方的小标签 */
  kicker?: ReactNode;
  /** 标题右侧的摘要 / 操作（折叠后自动隐藏） */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /** 默认是否折叠（仅当未提供 collapsedKey 时生效） */
  defaultCollapsed?: boolean;
  /** 提供则把折叠状态持久化到 localStorage（key 应稳定） */
  collapsedKey?: string;
  /** 内容区自定义样式 */
  bodyStyle?: CSSProperties;
}

const STORAGE_PREFIX = "panel-collapsed:";

function readStored(key: string): boolean | null {
  try {
    const v = localStorage.getItem(STORAGE_PREFIX + key);
    if (v === null) return null;
    return v === "1";
  } catch {
    return null;
  }
}

/**
 * 可折叠卡片 —— 统一替代分散在各页的 <section className="panel">。
 * 点击右上角折叠按钮（或标题栏）收起内容，折叠后只保留标题栏。
 * 提供 collapsedKey 时，折叠偏好会记住（刷新后保持）。
 */
export function CollapsiblePanel({
  title,
  kicker,
  actions,
  children,
  className,
  defaultCollapsed = false,
  collapsedKey,
  bodyStyle,
}: CollapsiblePanelProps) {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (collapsedKey) {
      const stored = readStored(collapsedKey);
      if (stored !== null) return stored;
    }
    return defaultCollapsed;
  });

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      if (collapsedKey) {
        try {
          localStorage.setItem(STORAGE_PREFIX + collapsedKey, next ? "1" : "0");
        } catch {
          /* 隐私模式下 localStorage 可能不可用，忽略 */
        }
      }
      return next;
    });
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  return (
    <section
      className={
        "panel colpanel" +
        (collapsed ? " colpanel--collapsed" : "") +
        (className ? " " + className : "")
      }
    >
      <div
        className="panel__header colpanel__header"
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        onClick={toggle}
        onKeyDown={onKeyDown}
        style={{ cursor: "pointer" }}
      >
        <span className="colpanel__heading">
          {kicker != null && <span className="panel__kicker">{kicker}</span>}
          <h2 className="panel__title">{title}</h2>
        </span>
        {actions != null && <span className="colpanel__actions">{actions}</span>}
        <button
          type="button"
          className="colpanel__toggle"
          aria-label={collapsed ? "展开" : "折叠"}
          onClick={(e) => {
            e.stopPropagation();
            toggle();
          }}
        >
          {collapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
        </button>
      </div>
      {!collapsed && (
        <div className="colpanel__body" style={bodyStyle}>
          {children}
        </div>
      )}
    </section>
  );
}
