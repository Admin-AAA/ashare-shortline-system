import { useEffect, useRef } from "react";
import * as THREE from "three";

type Theme = "dark" | "light";

const PALETTE = {
  dark: {
    fog: 0x070a18,
    grid: 0x5a8fff,
    gridOpacity: 0.5,
    particleA: 0x4a5fff, // 靛蓝
    particleB: 0x5fffd4, // 青绿
    particleOpacity: 1.0,
    particleSize: 0.32,
  },
  light: {
    fog: 0xdcd4c4,
    grid: 0x4a7a8a,
    gridOpacity: 0.45,
    particleA: 0x3a5a8a,
    particleB: 0x4a8a7a,
    particleOpacity: 0.7,
    particleSize: 0.22,
  },
} as const;

function currentTheme(): Theme {
  return (document.documentElement.dataset.theme as Theme) === "light" ? "light" : "dark";
}

/**
 * 数字海洋背景 v2 — 向远方无限延伸的发光网格走廊
 *
 * 关键设计：
 * - 网格 280(宽) × 700(长)，48×140 细分，沿 z 轴铺到极远
 * - 相机低角度 (0, 3, 35) 看向 (0, 0, -250)，像站在海面看向天际线
 * - 雾 near=15 far=320，远处网格逐格淡出，最终隐入虚空
 * - 波浪 sin(z*0.05 - t*1.1)，波峰随 t 向 +z 推进（从远方涌来）
 * - 两层波 + 横向次波，48 格密度让推进感连续可见
 * - 500 粒子在海面之上 0~14 浮游，靛蓝→青绿渐变
 * - 相机呼吸：x = sin(t*0.05)*1.6, y = 3 + sin(t*0.08)*0.35
 */
export default function StarfieldBackground() {
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();

    const camera = new THREE.PerspectiveCamera(
      72,
      mount.clientWidth / mount.clientHeight,
      0.1,
      1000,
    );
    camera.position.set(0, 3, 35);
    camera.lookAt(0, 0, -250);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const fog = new THREE.Fog(0x070a18, 15, 320);
    scene.fog = fog;

    // ── 发光网格：长走廊形，48×140 细分 ──
    // PlaneGeometry(w, h, segW, segH) 默认在 xy 平面，旋转 -PI/2 后
    // 原 y 轴 → 世界 z 轴，所以 segH 对应纵深方向
    const gridGeo = new THREE.PlaneGeometry(280, 700, 48, 140);
    const gridMat = new THREE.MeshBasicMaterial({
      color: 0x5a8fff,
      wireframe: true,
      transparent: true,
      opacity: 0.5,
      fog: true,
    });
    const grid = new THREE.Mesh(gridGeo, gridMat);
    grid.rotation.x = -Math.PI / 2;
    grid.position.set(0, 0, -300); // 中心在 z=-300，覆盖 z=+50 到 z=-650
    scene.add(grid);

    const positions = gridGeo.attributes.position;
    const basePositions = new Float32Array(positions.array.length);
    basePositions.set(positions.array as Float32Array);

    // ── 粒子：500 颗光尘在海面之上飘浮 ──
    const particleCount = 500;
    const particlePositions = new Float32Array(particleCount * 3);
    const particleColors = new Float32Array(particleCount * 3);
    const particleSpeeds = new Float32Array(particleCount);
    const particleWobble = new Float32Array(particleCount * 2);

    const colorA = new THREE.Color(PALETTE.dark.particleA);
    const colorB = new THREE.Color(PALETTE.dark.particleB);
    const tmpColor = new THREE.Color();

    const initParticle = (i: number) => {
      particlePositions[i * 3] = (Math.random() - 0.5) * 200;       // x: -100~100
      particlePositions[i * 3 + 1] = Math.random() * 14;             // y: 0~14
      particlePositions[i * 3 + 2] = -20 - Math.random() * 400;      // z: -20~-420
      particleSpeeds[i] = 0.008 + Math.random() * 0.025;
      particleWobble[i * 2] = Math.random() * Math.PI * 2;
      particleWobble[i * 2 + 1] = 0.3 + Math.random() * 0.6;
    };

    for (let i = 0; i < particleCount; i++) {
      initParticle(i);
      const yNorm = particlePositions[i * 3 + 1] / 14;
      tmpColor.copy(colorA).lerp(colorB, yNorm);
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
      fog.near = 15;
      fog.far = 320;
      gridMat.color.setHex(c.grid);
      gridMat.opacity = c.gridOpacity;
      particleMat.opacity = c.particleOpacity;
      particleMat.size = c.particleSize;
      colorA.setHex(c.particleA);
      colorB.setHex(c.particleB);
      for (let i = 0; i < particleCount; i++) {
        const yNorm = particlePositions[i * 3 + 1] / 14;
        tmpColor.copy(colorA).lerp(colorB, yNorm);
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

      // ── 网格波浪：从远方（-z）推向近前（+z）
      // PlaneGeometry 旋转前，原 y 轴对应世界 z 轴
      // sin(y*0.05 - t*1.1) 当 t 增加时，相同相位的 y 值变大 → 波峰向 +y 移动 → 世界 +z = 朝相机涌来
      const posArr = positions.array as Float32Array;
      for (let i = 0; i < posArr.length; i += 3) {
        const x = basePositions[i];
        const y = basePositions[i + 1];
        const wave1 = Math.sin(y * 0.05 - t * 1.1) * 2.4;
        const wave2 = Math.sin(y * 0.10 - t * 0.75 + 1.6) * 1.2;
        const wave3 = Math.sin(x * 0.04 + t * 0.4) * 0.4;
        posArr[i + 2] = wave1 + wave2 + wave3;
      }
      positions.needsUpdate = true;

      // ── 粒子上升 + 横向漂移 ──
      const pArr = particleGeo.attributes.position.array as Float32Array;
      for (let i = 0; i < particleCount; i++) {
        pArr[i * 3 + 1] += particleSpeeds[i];
        pArr[i * 3] += Math.sin(t * 0.5 + particleWobble[i * 2]) * particleWobble[i * 2 + 1] * 0.008;
        if (pArr[i * 3 + 1] > 14) {
          initParticle(i);
          pArr[i * 3 + 1] = 0;
        }
        const yNorm = pArr[i * 3 + 1] / 14;
        tmpColor.copy(colorA).lerp(colorB, yNorm);
        particleColors[i * 3] = tmpColor.r;
        particleColors[i * 3 + 1] = tmpColor.g;
        particleColors[i * 3 + 2] = tmpColor.b;
      }
      particleGeo.attributes.position.needsUpdate = true;
      particleGeo.attributes.color.needsUpdate = true;

      // ── 相机呼吸：缓慢左右摆动 + 微微上下起伏 ──
      camera.position.x = Math.sin(t * 0.05) * 1.6;
      camera.position.y = 3 + Math.sin(t * 0.08) * 0.35;
      camera.lookAt(0, 0, -250);

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
