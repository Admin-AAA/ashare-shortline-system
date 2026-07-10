import { type CSSProperties, type ReactNode } from "react";

interface PanelProps {
  /** 卡片标题（可含图标，支持 ReactNode） */
  title: ReactNode;
  /** 标题上方的小标签 */
  kicker?: ReactNode;
  /** 标题右侧的摘要 / 操作 */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /** 内容区自定义样式 */
  bodyStyle?: CSSProperties;
}

/**
 * 通用卡片面板 —— 统一的标题栏 + 内容区结构。
 * 固定展开渲染，不提供折叠交互。
 */
export function Panel({
  title,
  kicker,
  actions,
  children,
  className,
  bodyStyle,
}: PanelProps) {
  return (
    <section className={"panel" + (className ? " " + className : "")}>
      <div className="panel__header">
        <span className="panel__heading">
          {kicker != null && <span className="panel__kicker">{kicker}</span>}
          <h2 className="panel__title">{title}</h2>
        </span>
        {actions != null && <span className="panel__actions">{actions}</span>}
      </div>
      <div className="panel__body" style={bodyStyle}>
        {children}
      </div>
    </section>
  );
}
