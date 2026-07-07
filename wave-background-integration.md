# 动态波浪背景接入 frontend-v2（含黑白主题适配）

将 `Downloads/wave-background-copy/wave-background.html` 的 Three.js 波浪动态背景，移植进当前系统前端 `frontend-v2`，并适配深色/浅色双主题。

## 改了什么

### 1. `frontend-v2/src/components/WaveBackground.tsx`（占位 → 真实实现）
- 原文件是 `return null` 的占位。现改为完整实现：
  - 改用项目依赖的 `three@0.185`（**不再内联那 0.4MB 的压缩 Three.js**）。
  - 线框波浪面（`PlaneGeometry 200×200×48×48`，XZ 正弦波动）+ 500 个加性混合粒子。
  - 通过 `theme` prop 实时切换配色（`fog / 材质色 / 粒子色与不透明度`），**不重建 WebGL 上下文**，切换无闪烁。
  - 尊重 `prefers-reduced-motion`：仅渲染静态帧、不跑动画循环。
  - WebGL 不可用时降级为容器 CSS 渐变背景（不崩溃）。

### 2. `frontend-v2/src/styles/wave-background.css`（新增）
- `.wave-bg` 固定全屏 `z-index:0`、`pointer-events:none`、`isolation:isolate`。
- **浅色主题**：冷雾蓝渐变 + `mix-blend-mode: multiply`（与源文件视觉一致）。
- **深色主题**：深海极光径向渐变 + `mix-blend-mode: screen`（青蓝网格/粒子在深海上发光）。
- canvas 淡入动画。

### 3. `frontend-v2/src/styles/shell.css`
- `.app-shell` 改为 `background: transparent` + `position: relative; z-index:1`，让固定波浪层透出（`.app-main` 本就透明，卡片自带 surface；`body --canvas` 作降级底色）。

## 如何查看
```bash
cd frontend-v2
npm run dev          # 打开 http://localhost:5174
# 点右上角 月亮/太阳 图标切换主题，波浪背景会随之在 multiply(浅) ↔ screen(深) 间平滑切换
```

## 验证情况
- `tsc --noEmit` 通过（exit 0）。
- 用 esbuild 单独 bundle 组件 + three，确认所用 `THREE.*` API 在 0.185 全部存在（2.2MB 成功）。
- **像素级浏览器验证未做**：本环境未安装 `browser-use`/Playwright，无法截图。功能正确性已通过类型检查与打包验证。

## 注意（非本次范围）
- 当前 `npm run build` 会因 `src/pages/RecordsPage.tsx:46` 引用未定义的 `StockAnalysisDetailView`（同文件 519 行有定义，疑似作用域问题）而失败——这是**预先存在的 bug**，与本次波浪背景改动无关，未擅自改动。如需完整 `npm run build` 通过，需先修 RecordsPage。
