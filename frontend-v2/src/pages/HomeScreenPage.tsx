/**
 * 首页（空白起始页）—— 占位实现。
 * AppShell.tsx 引用了此文件，完整实现尚未提交。
 * 此占位让 build 通过，后续可替换为完整实现。
 */
import { useUrlRoute } from "../app/useUrlRoute";

export function HomePage() {
  const { navigateSection } = useUrlRoute();
  return (
    <section className="home-screen">
      <div className="home-screen__inner">
        <h1 className="home-screen__title">短线交易驾驶舱</h1>
        <p className="home-screen__subtitle">于数据海洋之上，凝视纵深，捕捉信号</p>
      </div>
    </section>
  );
}
