import { useEffect, useRef } from "react";
import * as THREE from "three";

type Theme = "dark" | "light";

const PALETTE = {
  dark: {
    fog: 0x05070d,
    grid: 0x2a4a7a,
    gridOpacity: 0.38,
    particle: 0x6ab4ff,
    particleOpacity: 0.85,
    particleSize: 0.18,
  },
  light: {
    fog: 0xe8e4dc,
    grid: 0x9a8e7a,
    gridOpacity: 0.5,
    particle: 0x8a7a5a,
    particleOpacity: 0.7,
    particleSize: 0.16,
  },
} as const;

function currentTheme(): Theme {
  return (document.documentElement.dataset.theme as Theme) === "light" ? "light" : "dark";
}

/**
 * 纵深远眺背景。
 * - 场景雾：near=0.028(暗) / 0.015(亮)，far=1000，近乎从镜头前即刻起雾，极缓淡出至无限远。
 * - 两块 200×200 线框网格沿 Z 轴以 time×9 速度循环滚动（25 单位循环）。
 * - 宽场相机：FOV=75°，z=20 居中后拉，略抬高俯视，容纳 90 单位纵深粒子场与网格。
 * - 500 粒子随机分布在 90×22×90 立体空间，逐粒子雾化 + sizeAttenuation，近大远小、近实远虚。
 * 观感：站在无限延伸的发光网格平面之上，粒子如星尘飘浮，远方隐入雾中。
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
    camera.lookAt(0, 2, -30);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const fog = new THREE.Fog(0x05070d, 0.028, 1000);
    scene.fog = fog;

    const makeGrid = (zOffset: number) => {
      const geo = new THREE.PlaneGeometry(200, 200, 50, 50);
      const mat = new THREE.MeshBasicMaterial({
        color: 0x2a4a7a,
        wireframe: true,
        transparent: true,
        opacity: 0.38,
        fog: true,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(0, 0, zOffset);
      return mesh;
    };
    const grid1 = makeGrid(0);
    const grid2 = makeGrid(-25);
    scene.add(grid1, grid2);

    const particleCount = 500;
    const positions = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i++) {
      positions[i * 3] = (Math.random() - 0.5) * 90;
      positions[i * 3 + 1] = (Math.random() - 0.5) * 22;
      positions[i * 3 + 2] = (Math.random() - 0.5) * 90;
    }
    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const particleMat = new THREE.PointsMaterial({
      color: 0x6ab4ff,
      size: 0.18,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.85,
      fog: true,
      depthWrite: false,
    });
    const particles = new THREE.Points(particleGeo, particleMat);
    scene.add(particles);

    const applyTheme = (theme: Theme) => {
      const c = PALETTE[theme];
      fog.color.setHex(c.fog);
      fog.near = theme === "dark" ? 0.028 : 0.015;
      fog.far = 1000;
      const g1Mat = grid1.material as THREE.MeshBasicMaterial;
      const g2Mat = grid2.material as THREE.MeshBasicMaterial;
      g1Mat.color.setHex(c.grid);
      g1Mat.opacity = c.gridOpacity;
      g2Mat.color.setHex(c.grid);
      g2Mat.opacity = c.gridOpacity;
      particleMat.color.setHex(c.particle);
      particleMat.opacity = c.particleOpacity;
      particleMat.size = c.particleSize;
    };
    applyTheme(currentTheme());

    const clock = new THREE.Clock();
    let raf = 0;
    const animate = () => {
      const t = clock.getElapsedTime();
      const off = (t * 9) % 25;
      grid1.position.z = off;
      grid2.position.z = off - 25;
      particles.rotation.y = t * 0.015;
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
      grid1.geometry.dispose();
      (grid1.material as THREE.Material).dispose();
      grid2.geometry.dispose();
      (grid2.material as THREE.Material).dispose();
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
