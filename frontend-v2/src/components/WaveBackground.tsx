import { useEffect, useRef } from "react";
import * as THREE from "three";
import "../styles/wave-background.css";

/**
 * 波浪动态背景组件。
 * 移植自 wave-background.html（原文件内联了压缩版 Three.js，这里改用项目依赖的 three）。
 * - 浅色主题：冷雾蓝渐变 + multiply 混合，与原文件视觉一致。
 * - 深色主题：深海极光渐变 + screen 混合，让青蓝网格/粒子在深海上发光。
 * 主题通过 `theme` prop 实时切换，无需重建 WebGL 上下文（平滑、无闪烁）。
 */

type Theme = "dark" | "light";

interface Palette {
  fogColor: number;
  fogDensity: number;
  meshColor: number;
  meshOpacity: number;
  particleOpacity: number;
  colorStart: number;
  colorEnd: number;
}

// 浅色取自源文件；深色沿用源文件内 isLight=false 分支的配色（青蓝网格 + 蓝粒子）。
const PALETTES: Record<Theme, Palette> = {
  light: {
    fogColor: 15659775, // #EEF2F7
    fogDensity: 0.015,
    meshColor: 6514417, // #6370F1 靛蓝
    meshOpacity: 0.12,
    particleOpacity: 0.45,
    colorStart: 6514417,
    colorEnd: 440020, // #06B7D4 青
  },
  dark: {
    fogColor: 592139, // #090B0B
    fogDensity: 0.028,
    meshColor: 1357990, // #14B8E6 青
    meshOpacity: 0.22,
    particleOpacity: 0.65,
    colorStart: 1357990,
    colorEnd: 3900150, // #3B82F6 蓝
  },
};

interface SceneHandle {
  applyTheme: (next: Theme) => void;
}

export function WaveBackground({ theme }: { theme: Theme }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<SceneHandle | null>(null);
  const themeRef = useRef<Theme>(theme);
  themeRef.current = theme;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const host = container;

    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;
    const palette = PALETTES[themeRef.current];

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    } catch {
      // WebGL 不可用：保留容器渐变背景作为降级，不挂载 canvas。
      return;
    }

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(palette.fogColor, palette.fogDensity);

    const camera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
    camera.position.y = 5;
    camera.position.z = 20;
    camera.lookAt(0, 0, 0);

    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    container.appendChild(renderer.domElement);

    const geometry = new THREE.PlaneGeometry(200, 200, 48, 48);
    const positions = geometry.attributes.position as THREE.BufferAttribute;
    const vertexCount = positions.count;
    const basePositions = new Float32Array(vertexCount * 2);
    for (let i = 0; i < vertexCount; i++) {
      basePositions[i * 2] = positions.getX(i);
      basePositions[i * 2 + 1] = positions.getY(i);
    }

    const material = new THREE.MeshBasicMaterial({
      color: palette.meshColor,
      wireframe: true,
      transparent: true,
      opacity: palette.meshOpacity,
    });

    const meshA = new THREE.Mesh(geometry, material);
    meshA.rotation.x = -Math.PI / 2;
    meshA.position.y = -2;
    scene.add(meshA);

    const meshB = new THREE.Mesh(geometry, material);
    meshB.rotation.x = -Math.PI / 2;
    meshB.position.y = -2;
    meshB.position.z = -25;
    scene.add(meshB);

    const PARTICLE_COUNT = 500;
    const particleGeometry = new THREE.BufferGeometry();
    const particlePositions = new Float32Array(PARTICLE_COUNT * 3);
    const velocities = new Float32Array(PARTICLE_COUNT * 3);
    const colors = new Float32Array(PARTICLE_COUNT * 3);
    const colorStart = new THREE.Color(palette.colorStart);
    const colorEnd = new THREE.Color(palette.colorEnd);

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const offset = i * 3;
      particlePositions[offset] = (Math.random() - 0.5) * 90;
      particlePositions[offset + 1] = Math.random() * 22;
      particlePositions[offset + 2] = (Math.random() - 0.5) * 90;
      velocities[offset] = (Math.random() - 0.5) * 0.04;
      velocities[offset + 1] = 0.06 + Math.random() * 0.12;
      velocities[offset + 2] = (Math.random() - 0.5) * 0.04;
      const color = colorStart.clone().lerp(colorEnd, Math.random());
      colors[offset] = color.r;
      colors[offset + 1] = color.g;
      colors[offset + 2] = color.b;
    }

    particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
    particleGeometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    const particleMaterial = new THREE.PointsMaterial({
      size: 0.18,
      vertexColors: true,
      transparent: true,
      opacity: palette.particleOpacity,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true,
    });
    const particles = new THREE.Points(particleGeometry, particleMaterial);
    scene.add(particles);

    const clock = new THREE.Clock();
    let frameId = 0;

    function renderFrame() {
      const elapsed = clock.getElapsedTime();
      for (let i = 0; i < vertexCount; i++) {
        const x = basePositions[i * 2];
        const y = basePositions[i * 2 + 1];
        const z =
          Math.sin(x * 0.12 + elapsed * 1.4) * Math.cos(y * 0.12 + elapsed * 1.1) * 2.2 +
          Math.sin(x * 0.25 + y * 0.2 + elapsed * 2) * 0.9;
        positions.setZ(i, z);
      }
      positions.needsUpdate = true;

      const scroll = elapsed * 9;
      meshA.position.z = THREE.MathUtils.euclideanModulo(scroll, 25);
      meshB.position.z = THREE.MathUtils.euclideanModulo(scroll, 25) - 25;

      const particleArray = particleGeometry.attributes.position.array as Float32Array;
      for (let i = 0; i < PARTICLE_COUNT; i++) {
        const offset = i * 3;
        particleArray[offset] += velocities[offset];
        particleArray[offset + 1] += velocities[offset + 1];
        particleArray[offset + 2] += velocities[offset + 2];
        if (particleArray[offset + 1] > 24) {
          particleArray[offset + 1] = -2;
          particleArray[offset] = (Math.random() - 0.5) * 90;
          particleArray[offset + 2] = (Math.random() - 0.5) * 90;
        }
        if (Math.abs(particleArray[offset]) > 48) particleArray[offset] *= -0.95;
        if (Math.abs(particleArray[offset + 2]) > 48) particleArray[offset + 2] *= -0.95;
      }
      particleGeometry.attributes.position.needsUpdate = true;

      particles.rotation.y = elapsed * 0.08;
      particles.rotation.z = Math.sin(elapsed * 0.25) * 0.06;
      camera.position.x = Math.sin(elapsed * 0.35) * 2.8;
      camera.position.y = 5 + Math.sin(elapsed * 0.5) * 0.35;
      camera.lookAt(0, -0.4 + Math.sin(elapsed * 0.2) * 0.15, Math.sin(elapsed * 0.15) * 1.5);
      renderer.render(scene, camera);
    }

    function loop() {
      renderFrame();
      frameId = requestAnimationFrame(loop);
    }

    if (prefersReducedMotion) {
      renderFrame();
    } else {
      loop();
    }

    function resize() {
      const nextWidth = host.clientWidth || window.innerWidth;
      const nextHeight = host.clientHeight || window.innerHeight;
      camera.aspect = nextWidth / nextHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(nextWidth, nextHeight);
      if (prefersReducedMotion) renderFrame();
    }

    window.addEventListener("resize", resize);

    function applyTheme(next: Theme) {
      const p = PALETTES[next];
      const fog = scene.fog as THREE.FogExp2;
      fog.color.setHex(p.fogColor);
      fog.density = p.fogDensity;
      material.color.setHex(p.meshColor);
      material.opacity = p.meshOpacity;
      particleMaterial.opacity = p.particleOpacity;

      colorStart.setHex(p.colorStart);
      colorEnd.setHex(p.colorEnd);
      const colorAttr = particleGeometry.attributes.color as THREE.BufferAttribute;
      const arr = colorAttr.array as Float32Array;
      for (let i = 0; i < PARTICLE_COUNT; i++) {
        const offset = i * 3;
        const c = colorStart.clone().lerp(colorEnd, Math.random());
        arr[offset] = c.r;
        arr[offset + 1] = c.g;
        arr[offset + 2] = c.b;
      }
      colorAttr.needsUpdate = true;

      if (prefersReducedMotion) renderFrame();
    }

    sceneRef.current = { applyTheme };

    return () => {
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(frameId);
      if (renderer.domElement.parentNode === host) {
        host.removeChild(renderer.domElement);
      }
      geometry.dispose();
      material.dispose();
      particleGeometry.dispose();
      particleMaterial.dispose();
      renderer.dispose();
      sceneRef.current = null;
    };
  }, []);

  // 主题切换：实时更新配色与混合，不重建上下文（无闪烁）。
  useEffect(() => {
    sceneRef.current?.applyTheme(theme);
  }, [theme]);

  return <div ref={containerRef} className="wave-bg" data-theme={theme} aria-hidden="true" />;
}
