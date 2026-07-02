import { useEffect, useRef } from "react";
import * as THREE from "three";

type Theme = "dark" | "light";

const PALETTE = {
  dark: {
    fog: 0x050816,
    fogDensity: 0.032,
    grid: 0x2dd4bf,
    gridOpacity: 0.18,
    particleStart: 0x60a5fa,
    particleEnd: 0x2dd4bf,
    particleOpacity: 0.75,
    particleSize: 0.20,
  },
  light: {
    fog: 0xeef2ff,
    fogDensity: 0.015,
    grid: 0x3b82f6,
    gridOpacity: 0.10,
    particleStart: 0x3b82f6,
    particleEnd: 0x0d9488,
    particleOpacity: 0.42,
    particleSize: 0.18,
  },
} as const;

function currentTheme(): Theme {
  return (document.documentElement.dataset.theme as Theme) === "light" ? "light" : "dark";
}

function euclideanModulo(n: number, m: number) {
  return ((n % m) + m) % m;
}

/**
 * 数字海洋背景 v3 — 复刻参考实现的空灵水面效果
 *
 * 核心设计（来自 wave-background.html）：
 * - FogExp2 雾：light #eedfff / dark #09090b，密度 0.015/0.028
 * - 两块 200×200 网格（48×48 细分），位于 y=-2（相机下方，不会飘过头顶）
 * - 网格 z 轴循环滚动，25 单位周期，time×9 速度
 * - 波浪：sin(x*0.12 + t*1.4) * cos(y*0.12 + t*1.1) * 2.2 + sin(x*0.25 + y*0.2 + t*2) * 0.9
 * - 500 粒子在海面上方 x/z ±45、y 0~22 范围飘浮
 * - 相机 (0,5,20) lookAt(0,0,0)，呼吸摆动
 * - 网格与粒子均用低透明度，配合 CSS 浅色渐变背景 + mix-blend-mode: multiply
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
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const geometry = new THREE.PlaneGeometry(200, 200, 48, 48);
    const positions = geometry.attributes.position;
    const vertexCount = positions.count;
    const basePositions = new Float32Array(vertexCount * 2);
    for (let i = 0; i < vertexCount; i++) {
      basePositions[i * 2] = positions.getX(i);
      basePositions[i * 2 + 1] = positions.getY(i);
    }

    const gridMat = new THREE.MeshBasicMaterial({
      color: 0x6361f1,
      wireframe: true,
      transparent: true,
      opacity: 0.12,
      fog: true,
    });

    const meshA = new THREE.Mesh(geometry, gridMat);
    meshA.rotation.x = -Math.PI / 2;
    meshA.position.y = -2;
    scene.add(meshA);

    const meshB = new THREE.Mesh(geometry, gridMat);
    meshB.rotation.x = -Math.PI / 2;
    meshB.position.y = -2;
    meshB.position.z = -25;
    scene.add(meshB);

    // 粒子
    const particleCount = 500;
    const particlePositions = new Float32Array(particleCount * 3);
    const velocities = new Float32Array(particleCount * 3);
    const particleColors = new Float32Array(particleCount * 3);
    const colorStart = new THREE.Color(PALETTE.light.particleStart);
    const colorEnd = new THREE.Color(PALETTE.light.particleEnd);
    const tmpColor = new THREE.Color();

    const initParticle = (i: number) => {
      particlePositions[i * 3] = (Math.random() - 0.5) * 90;
      particlePositions[i * 3 + 1] = Math.random() * 22;
      particlePositions[i * 3 + 2] = (Math.random() - 0.5) * 90;
      velocities[i * 3] = (Math.random() - 0.5) * 0.04;
      velocities[i * 3 + 1] = 0.06 + Math.random() * 0.12;
      velocities[i * 3 + 2] = (Math.random() - 0.5) * 0.04;
    };

    for (let i = 0; i < particleCount; i++) {
      initParticle(i);
      tmpColor.copy(colorStart).lerp(colorEnd, Math.random());
      particleColors[i * 3] = tmpColor.r;
      particleColors[i * 3 + 1] = tmpColor.g;
      particleColors[i * 3 + 2] = tmpColor.b;
    }

    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
    particleGeo.setAttribute("color", new THREE.BufferAttribute(particleColors, 3));
    const particleMat = new THREE.PointsMaterial({
      size: 0.18,
      vertexColors: true,
      transparent: true,
      opacity: 0.45,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true,
      fog: true,
      depthWrite: false,
    });
    const particles = new THREE.Points(particleGeo, particleMat);
    scene.add(particles);

    const applyTheme = (theme: Theme) => {
      const c = PALETTE[theme];
      scene.fog = new THREE.FogExp2(c.fog, c.fogDensity);
      gridMat.color.setHex(c.grid);
      gridMat.opacity = c.gridOpacity;
      particleMat.opacity = c.particleOpacity;
      particleMat.size = c.particleSize;
      colorStart.setHex(c.particleStart);
      colorEnd.setHex(c.particleEnd);
      for (let i = 0; i < particleCount; i++) {
        tmpColor.copy(colorStart).lerp(colorEnd, Math.random());
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

      // 网格水面涟漪
      const posArr = positions.array as Float32Array;
      for (let i = 0; i < vertexCount; i++) {
        const x = basePositions[i * 2];
        const y = basePositions[i * 2 + 1];
        const z =
          Math.sin(x * 0.12 + t * 1.4) * Math.cos(y * 0.12 + t * 1.1) * 2.2 +
          Math.sin(x * 0.25 + y * 0.2 + t * 2) * 0.9;
        positions.setZ(i, z);
      }
      positions.needsUpdate = true;

      // 网格循环滚动
      const scroll = t * 9;
      meshA.position.z = euclideanModulo(scroll, 25);
      meshB.position.z = euclideanModulo(scroll, 25) - 25;

      // 粒子飘浮
      const pArr = particleGeo.attributes.position.array as Float32Array;
      for (let i = 0; i < particleCount; i++) {
        const offset = i * 3;
        pArr[offset] += velocities[offset];
        pArr[offset + 1] += velocities[offset + 1];
        pArr[offset + 2] += velocities[offset + 2];
        if (pArr[offset + 1] > 24) {
          pArr[offset + 1] = -2;
          pArr[offset] = (Math.random() - 0.5) * 90;
          pArr[offset + 2] = (Math.random() - 0.5) * 90;
        }
        if (Math.abs(pArr[offset]) > 48) pArr[offset] *= -0.95;
        if (Math.abs(pArr[offset + 2]) > 48) pArr[offset + 2] *= -0.95;
      }
      particleGeo.attributes.position.needsUpdate = true;

      // 整体缓慢旋转 + 相机呼吸
      particles.rotation.y = t * 0.08;
      particles.rotation.z = Math.sin(t * 0.25) * 0.06;
      camera.position.x = Math.sin(t * 0.35) * 2.8;
      camera.position.y = 5 + Math.sin(t * 0.5) * 0.35;
      camera.lookAt(0, -0.4 + Math.sin(t * 0.2) * 0.15, Math.sin(t * 0.15) * 1.5);

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
      geometry.dispose();
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
