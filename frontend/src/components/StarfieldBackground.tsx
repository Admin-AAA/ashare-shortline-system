import { useEffect, useRef } from "react";
import * as THREE from "three";

type Theme = "dark" | "light";

const PALETTE = {
  dark: {
    fog: 0x0a0a1e,
    grid: 0x6a9fff,
    gridOpacity: 0.55,
    particleA: 0x4a5fff, // 靛蓝
    particleB: 0x5fffd4, // 青绿
    particleOpacity: 1.0,
    particleSize: 0.28,
  },
  light: {
    fog: 0xdcd4c4,
    grid: 0x4a7a8a,
    gridOpacity: 0.5,
    particleA: 0x3a5a8a,
    particleB: 0x4a8a7a,
    particleOpacity: 0.7,
    particleSize: 0.18,
  },
} as const;

function currentTheme(): Theme {
  return (document.documentElement.dataset.theme as Theme) === "light" ? "light" : "dark";
}

/**
 * 数字海洋背景。
 * - 发光网格平面 200×200，48×48 细分，顶点 z 起伏形成波浪
 *   两层交错波峰 + 横向次波，从远方（y=-100）向近前（y=100）推进
 * - 500 粒子从底部（y=-10）向上飘浮到顶部（y=8），到顶重置回底部
 *   颜色在靛蓝 #4a5fff 与青绿 #5fffd4 之间按 y 高度过渡
 * - 场景雾：near=0.028(暗)/0.015(亮)，far=1000，远方隐入雾中
 * - 宽场相机 FOV=75°，z=20 居中后拉，呼吸式左右摆动 + 上下起伏
 * - 背景底色与极光晕由 CSS 渲染（见 cosmic.css 的 .starfield-bg）
 * - canvas 用 mix-blend-mode 与背景融合，暗色 screen / 亮色 multiply
 */
export default function StarfieldBackground() {
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();

    const camera = new THREE.PerspectiveCamera(
      75,
      mount.clientWidth / mount.clientHeight,
      0.1,
      1000,
    );
    camera.position.set(0, 5, 20);
    camera.lookAt(0, 0, -30);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const fog = new THREE.Fog(0x0a0a1e, 0.028, 1000);
    scene.fog = fog;

    // ── 发光网格：48×48 密度 ──
    const gridGeo = new THREE.PlaneGeometry(200, 200, 48, 48);
    const gridMat = new THREE.MeshBasicMaterial({
      color: 0x4a6fff,
      wireframe: true,
      transparent: true,
      opacity: 0.42,
      fog: true,
    });
    const grid = new THREE.Mesh(gridGeo, gridMat);
    grid.rotation.x = -Math.PI / 2;
    grid.position.set(0, 0, -20);
    scene.add(grid);

    const positions = gridGeo.attributes.position;
    const basePositions = new Float32Array(positions.array.length);
    basePositions.set(positions.array as Float32Array);

    // ── 粒子：500 颗光尘向上飘浮 ──
    const particleCount = 500;
    const particlePositions = new Float32Array(particleCount * 3);
    const particleColors = new Float32Array(particleCount * 3);
    const particleSpeeds = new Float32Array(particleCount);
    const particleWobble = new Float32Array(particleCount * 2); // phase, amp

    const colorA = new THREE.Color(PALETTE.dark.particleA);
    const colorB = new THREE.Color(PALETTE.dark.particleB);
    const tmpColor = new THREE.Color();

    const initParticle = (i: number) => {
      particlePositions[i * 3] = (Math.random() - 0.5) * 80;
      particlePositions[i * 3 + 1] = -10 + Math.random() * 18;
      particlePositions[i * 3 + 2] = (Math.random() - 0.5) * 80;
      particleSpeeds[i] = 0.01 + Math.random() * 0.035;
      particleWobble[i * 2] = Math.random() * Math.PI * 2;
      particleWobble[i * 2 + 1] = 0.2 + Math.random() * 0.5;
    };

    for (let i = 0; i < particleCount; i++) {
      initParticle(i);
      const yNorm = (particlePositions[i * 3 + 1] + 10) / 18;
      tmpColor.copy(colorA).lerp(colorB, Math.max(0, Math.min(1, yNorm)));
      particleColors[i * 3] = tmpColor.r;
      particleColors[i * 3 + 1] = tmpColor.g;
      particleColors[i * 3 + 2] = tmpColor.b;
    }
    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
    particleGeo.setAttribute("color", new THREE.BufferAttribute(particleColors, 3));
    const particleMat = new THREE.PointsMaterial({
      size: PALETTE.dark.particleSize,
      sizeAttenuation: true,
      transparent: true,
      opacity: PALETTE.dark.particleOpacity,
      vertexColors: true,
      fog: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const particles = new THREE.Points(particleGeo, particleMat);
    scene.add(particles);

    const applyTheme = (theme: Theme) => {
      const c = PALETTE[theme];
      fog.color.setHex(c.fog);
      fog.near = theme === "dark" ? 0.028 : 0.015;
      fog.far = 1000;
      gridMat.color.setHex(c.grid);
      gridMat.opacity = c.gridOpacity;
      particleMat.opacity = c.particleOpacity;
      particleMat.size = c.particleSize;
      colorA.setHex(c.particleA);
      colorB.setHex(c.particleB);
      for (let i = 0; i < particleCount; i++) {
        const yNorm = (particlePositions[i * 3 + 1] + 10) / 18;
        tmpColor.copy(colorA).lerp(colorB, Math.max(0, Math.min(1, yNorm)));
        particleColors[i * 3] = tmpColor.r;
        particleColors[i * 3 + 1] = tmpColor.g;
        particleColors[i * 3 + 2] = tmpColor.b;
      }
      particleGeo.attributes.color.needsUpdate = true;
    };
    applyTheme(currentTheme());

    const clock = new THREE.Clock();
    let raf = 0;
    const animate = () => {
      const t = clock.getElapsedTime();

      // ── 网格波浪：从远方推向近前，两层交错 + 横向次波 ──
      const posArr = positions.array as Float32Array;
      for (let i = 0; i < posArr.length; i += 3) {
        const x = basePositions[i];
        const y = basePositions[i + 1];
        // 波浪沿原 y 轴推进（rotate 后即沿世界 z 朝相机方向）
        const wave1 = Math.sin(y * 0.08 + t * 1.4) * 2.2;
        const wave2 = Math.sin(y * 0.14 + t * 1.0 + 1.6) * 1.1;
        const wave3 = Math.sin(x * 0.05 + t * 0.55) * 0.5;
        posArr[i + 2] = wave1 + wave2 + wave3;
      }
      positions.needsUpdate = true;

      // ── 粒子上升 + 横向漂移 + 颜色渐变 ──
      const pArr = particleGeo.attributes.position.array as Float32Array;
      for (let i = 0; i < particleCount; i++) {
        pArr[i * 3 + 1] += particleSpeeds[i];
        // 横向轻微摆动
        pArr[i * 3] += Math.sin(t * 0.6 + particleWobble[i * 2]) * particleWobble[i * 2 + 1] * 0.01;
        if (pArr[i * 3 + 1] > 8) {
          initParticle(i);
          pArr[i * 3 + 1] = -10;
        }
        const yNorm = (pArr[i * 3 + 1] + 10) / 18;
        tmpColor.copy(colorA).lerp(colorB, Math.max(0, Math.min(1, yNorm)));
        particleColors[i * 3] = tmpColor.r;
        particleColors[i * 3 + 1] = tmpColor.g;
        particleColors[i * 3 + 2] = tmpColor.b;
      }
      particleGeo.attributes.position.needsUpdate = true;
      particleGeo.attributes.color.needsUpdate = true;

      // ── 相机呼吸：左右摆动 + 上下起伏 ──
      camera.position.x = Math.sin(t * 0.08) * 2.2;
      camera.position.y = 5 + Math.sin(t * 0.12) * 0.4;
      camera.lookAt(0, 0, -30);

      renderer.render(scene, camera);
      raf = requestAnimationFrame(animate);
    };
    animate();

    const resizeObserver = new ResizeObserver(() => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (w === 0 || h === 0) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    resizeObserver.observe(mount);

    const themeObserver = new MutationObserver(() => applyTheme(currentTheme()));
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    return () => {
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      themeObserver.disconnect();
      gridGeo.dispose();
      gridMat.dispose();
      particleGeo.dispose();
      particleMat.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, []);

  return <div ref={mountRef} className="starfield-bg" aria-hidden="true" />;
}
