/* MultiShop — deterministic golden particle rivers. */
(function () {
  "use strict";

  const STORAGE_KEY = "ms_particles_enabled";
  const OLD_STORAGE_KEY = "ms_dragon_background_enabled";
  const SEED = 291029;
  const TAU = Math.PI * 2;
  const PARTICLE_STRIDE = 12;
  const RIVER_COUNT = 3;
  const RIVER_RADII_X = [0.44, 0.30, 0.16];
  const RIVER_RADII_Y = [0.40, 0.26, 0.14];
  const RIVER_WIDTHS = [0.11, 0.08, 0.05];
  const RIVER_PERIODS = [140, 100, 70];
  const RIVER_DIRECTIONS = [1, -1, 1];
  const RIVER_WAVE_AMPLITUDES = [15, 20, 15];
  const RIVER_WAVE_RATES = [0.074, 0.091, 0.113];
  const RIVER_WAVE_PHASES = [0.2, 2.3, 4.1];
  const RIVER_BREATHE_PERIODS = [32, 25, 38];
  const HIGHLIGHT_PERIODS = [6.8, 8.2, 9.6];
  const SIZE_MIN = [2, 3.5, 6, 10];
  const SIZE_RANGE = [1, 2, 3, 6];
  const SPRITE_SIZE = 64;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const canvas = document.createElement("canvas");
  canvas.className = "particles-background-canvas";
  canvas.setAttribute("aria-hidden", "true");
  canvas.setAttribute("role", "presentation");
  document.body.insertBefore(canvas, document.body.firstChild);

  const context = canvas.getContext("2d", { alpha: true });
  if (!context) {
    console.error("Не вдалося створити canvas для золотих частинок.");
    return;
  }

  let width = Math.max(1, window.innerWidth);
  let height = Math.max(1, window.innerHeight);
  let pixelRatio = 1;
  let count = 0;
  let particles = null;
  let particleOrder = null;
  let bucketOffsets = new Uint32Array(17);
  let sprites = new Array(16);
  let glowCanvas = null;
  let glowContext = null;
  let wideCanvas = null;
  let wideContext = null;
  let glowSprites = new Array(4);
  let narrowGlowEnabled = true;
  let wideGlowEnabled = true;
  let weakGlow = false;
  const pointScratch = new Float32Array(2);
  const riverHalfWidths = new Float32Array(RIVER_COUNT);
  let frame = 0;
  let loopAllowed = false;
  let stopTimer = 0;
  let toggle = null;
  let toggleObserver = null;
  let slowFrames = 0;
  let adaptiveLevel = 1;
  let enabled = true;

  function mulberry32(seed) {
    let state = seed >>> 0;
    return function random() {
      state += 0x6d2b79f5;
      let value = state;
      value = Math.imul(value ^ (value >>> 15), value | 1);
      value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
  }

  function gaussian(random) {
    const u = Math.max(0.0001, random());
    const v = random();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(TAU * v);
  }

  function readState() {
    try {
      let stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored === null) {
        const old = window.localStorage.getItem(OLD_STORAGE_KEY);
        if (old !== null) stored = old;
      }
      window.localStorage.removeItem(OLD_STORAGE_KEY);
      if (stored !== null) return stored !== "false";
    } catch (_error) {
      // Local storage is optional.
    }
    return false;
  }

  enabled = readState();

  function persistState() {
    try {
      window.localStorage.setItem(STORAGE_KEY, String(enabled));
    } catch (_error) {
      // State can remain in memory for this tab.
    }
  }

  function desiredCount() {
    const base = width < 1100 ? 1450 : (width >= 1920 ? 3700 : 3450);
    return Math.max(Math.round(base * 0.5), Math.round(base * adaptiveLevel));
  }

  function buildParticles() {
    count = desiredCount();
    particles = new Float32Array(count * PARTICLE_STRIDE);
    particleOrder = new Uint32Array(count);
    bucketOffsets.fill(0);
    const random = mulberry32(SEED);
    const dustCount = Math.round(count * 0.05);
    const riverParticles = count - dustCount;
    const riverCounts = [
      Math.round(riverParticles * 0.49),
      Math.round(riverParticles * 0.32),
      0,
    ];
    riverCounts[2] = riverParticles - riverCounts[0] - riverCounts[1];
    let index = 0;

    for (let river = 0; river < RIVER_COUNT; river += 1) {
      for (let local = 0; local < riverCounts[river]; local += 1) {
        const offset = index * PARTICLE_STRIDE;
        const distribution = random();
        const category = distribution < 0.55 ? 0
          : distribution < 0.87 ? 1
            : distribution < 0.97 ? 2 : 3;
        const spriteIndex = category * 4 + Math.floor(random() * 4);
        particles[offset] = random() * TAU;
        particles[offset + 1] = Math.max(-1.25, Math.min(1.25, gaussian(random) / 2.7));
        particles[offset + 2] = 0.92 + random() * 0.16;
        particles[offset + 3] = random() * TAU;
        particles[offset + 4] = spriteIndex;
        particles[offset + 5] = river;
        particles[offset + 6] = Math.floor(random() * 3);
        particles[offset + 7] = 1.5 + random() * 3.5;
        particles[offset + 8] = 0.55 + random() * 0.45;
        particles[offset + 9] = random();
        particles[offset + 11] = category === 3 && random() < 0.68 ? 1 : 0;
        index += 1;
      }
    }

    for (let local = 0; local < dustCount; local += 1) {
      const offset = index * PARTICLE_STRIDE;
      const spriteIndex = Math.floor(random() * 4);
      particles[offset] = random() * TAU;
      particles[offset + 3] = random() * TAU;
      particles[offset + 4] = spriteIndex;
      particles[offset + 5] = -1;
      particles[offset + 7] = 2 + random() * 3;
      particles[offset + 8] = 0.2 + random() * 0.2;
      particles[offset + 9] = random();
      particles[offset + 10] = random();
      index += 1;
    }

    bucketOffsets.fill(0);
    for (let i = 0; i < count; i += 1) {
      bucketOffsets[particles[i * PARTICLE_STRIDE + 4] + 1] += 1;
    }
    for (let i = 1; i < bucketOffsets.length; i += 1) {
      bucketOffsets[i] += bucketOffsets[i - 1];
    }
    const sortedCursors = bucketOffsets.slice(0, 16);
    for (let i = 0; i < count; i += 1) {
      const spriteIndex = particles[i * PARTICLE_STRIDE + 4];
      particleOrder[sortedCursors[spriteIndex]] = i;
      sortedCursors[spriteIndex] += 1;
    }
  }

  function createSprite(category, variant) {
    const sprite = document.createElement("canvas");
    sprite.width = SPRITE_SIZE;
    sprite.height = SPRITE_SIZE;
    const spriteContext = sprite.getContext("2d");
    const center = SPRITE_SIZE / 2;
    const gradient = spriteContext.createRadialGradient(center, center, 0, center, center, 29);
    const brightness = [0.74, 0.84, 0.93, 1][variant];
    gradient.addColorStop(0, "rgba(255,246,200," + brightness + ")");
    gradient.addColorStop(0.13, "rgba(255,238,166," + (brightness * 0.96) + ")");
    gradient.addColorStop(0.38, "rgba(255,210,90," + (brightness * 0.58) + ")");
    gradient.addColorStop(0.72, "rgba(224,160,42," + (brightness * 0.18) + ")");
    gradient.addColorStop(1, "rgba(224,160,42,0)");

    if (category === 3) {
      spriteContext.save();
      spriteContext.globalAlpha = 0.48 + variant * 0.1;
      spriteContext.strokeStyle = "#fff2b0";
      spriteContext.lineWidth = 1.1;
      spriteContext.lineCap = "round";
      spriteContext.beginPath();
      spriteContext.moveTo(center, 8);
      spriteContext.lineTo(center, SPRITE_SIZE - 8);
      spriteContext.moveTo(8, center);
      spriteContext.lineTo(SPRITE_SIZE - 8, center);
      spriteContext.stroke();
      spriteContext.restore();
    }
    spriteContext.fillStyle = gradient;
    spriteContext.fillRect(0, 0, SPRITE_SIZE, SPRITE_SIZE);
    return sprite;
  }

  function buildSprites() {
    for (let category = 0; category < 4; category += 1) {
      for (let variant = 0; variant < 4; variant += 1) {
        sprites[category * 4 + variant] = createSprite(category, variant);
      }
    }
  }

  function createGlowSprite(size) {
    const sprite = document.createElement("canvas");
    sprite.width = 128;
    sprite.height = 128;
    const spriteContext = sprite.getContext("2d");
    const center = 64;
    const radius = size * 2;
    const gradient = spriteContext.createRadialGradient(center, center, 0, center, center, radius);
    gradient.addColorStop(0, "rgba(255,240,176,1)");
    gradient.addColorStop(0.14, "rgba(255,207,90,.92)");
    gradient.addColorStop(0.42, "rgba(255,207,90,.48)");
    gradient.addColorStop(0.78, "rgba(201,138,30,.16)");
    gradient.addColorStop(1, "rgba(201,138,30,0)");
    spriteContext.fillStyle = gradient;
    spriteContext.fillRect(0, 0, 128, 128);
    return sprite;
  }

  function buildGlowSprites() {
    glowSprites[0] = createGlowSprite(12);
    glowSprites[1] = createGlowSprite(16);
    glowSprites[2] = createGlowSprite(22);
    glowSprites[3] = createGlowSprite(28);
  }

  function resizeCanvas() {
    width = Math.max(1, window.innerWidth);
    height = Math.max(1, window.innerHeight);
    pixelRatio = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);

    if (!glowCanvas) {
      glowCanvas = document.createElement("canvas");
      glowContext = glowCanvas.getContext("2d", { alpha: true });
      wideCanvas = document.createElement("canvas");
      wideContext = wideCanvas.getContext("2d", { alpha: true });
    }

    const narrowDivisor = width < 1100 ? 6 : 4;
    glowCanvas.width = Math.max(1, Math.round(width / narrowDivisor * pixelRatio));
    glowCanvas.height = Math.max(1, Math.round(height / narrowDivisor * pixelRatio));
    wideCanvas.width = Math.max(1, Math.round(width / 12 * pixelRatio));
    wideCanvas.height = Math.max(1, Math.round(height / 12 * pixelRatio));
    glowContext.imageSmoothingEnabled = true;
    glowContext.imageSmoothingQuality = "high";
    wideContext.imageSmoothingEnabled = true;
    wideContext.imageSmoothingQuality = "high";

    const cpuWeak = (navigator.hardwareConcurrency || 8) <= 4;
    weakGlow = cpuWeak;
    wideGlowEnabled = !cpuWeak;
    narrowGlowEnabled = true;
    buildGlowSprites();
    buildParticles();
  }

  function riverWave(angle, river, elapsed) {
    const amp = RIVER_WAVE_AMPLITUDES[river];
    return Math.sin(angle * 2 + elapsed * RIVER_WAVE_RATES[river] +
      RIVER_WAVE_PHASES[river]) * amp +
      Math.sin(angle * 3 - elapsed * RIVER_WAVE_RATES[river] * 0.57 +
        RIVER_WAVE_PHASES[river] * 1.7) * amp * 0.24 +
      Math.sin(angle * 5 + elapsed * RIVER_WAVE_RATES[river] * 0.31 +
        RIVER_WAVE_PHASES[river] * 0.6) * amp * 0.12;
  }

  function riverPoint(angle, river, elapsed, centerX, centerY, offset) {
    const radiusX = width * RIVER_RADII_X[river];
    const radiusY = height * RIVER_RADII_Y[river];
    const breathe = 1 + Math.sin(elapsed * TAU / RIVER_BREATHE_PERIODS[river] +
      RIVER_WAVE_PHASES[river]) * 0.15;
    const harmonicX =
      Math.sin(angle * 2 + elapsed * RIVER_WAVE_RATES[river] * 0.31 +
        RIVER_WAVE_PHASES[river]) * radiusX * 0.014 +
      Math.sin(angle * 3 - elapsed * RIVER_WAVE_RATES[river] * 0.23 +
        RIVER_WAVE_PHASES[river] * 1.7) * radiusX * 0.018 +
      Math.sin(angle * 5 + elapsed * RIVER_WAVE_RATES[river] * 0.13 +
        RIVER_WAVE_PHASES[river] * 0.6) * radiusX * 0.008;
    const harmonicY =
      Math.cos(angle * 2 - elapsed * RIVER_WAVE_RATES[river] * 0.27 +
        RIVER_WAVE_PHASES[river]) * radiusY * 0.014 +
      Math.sin(angle * 3 + elapsed * RIVER_WAVE_RATES[river] * 0.19 +
        RIVER_WAVE_PHASES[river] * 1.3) * radiusY * 0.018 +
      Math.cos(angle * 5 - elapsed * RIVER_WAVE_RATES[river] * 0.11 +
        RIVER_WAVE_PHASES[river]) * radiusY * 0.008;
    const cosine = Math.cos(angle);
    const sine = Math.sin(angle);
    const normalX = -sine / radiusX;
    const normalY = cosine / radiusY;
    const normalLength = Math.sqrt(normalX * normalX + normalY * normalY) || 1;
    const normalOffset = riverWave(angle, river, elapsed) + offset;
    pointScratch[0] = centerX + cosine * radiusX * breathe + harmonicX +
      normalX / normalLength * normalOffset;
    pointScratch[1] = centerY + sine * radiusY * breathe + harmonicY +
      normalY / normalLength * normalOffset;
  }

  function drawFrame(nowSeconds) {
    context.clearRect(0, 0, width, height);
    context.globalCompositeOperation = "lighter";
    const centerX = width * 0.5;
    const centerY = height * 0.5;
    const minSide = Math.min(width, height);
    const elapsed = reducedMotion.matches ? 0 : nowSeconds;

    for (let river = 0; river < RIVER_COUNT; river += 1) {
      const breathe = 1 + Math.sin(elapsed * TAU / RIVER_BREATHE_PERIODS[river] +
        RIVER_WAVE_PHASES[river]) * 0.15;
      riverHalfWidths[river] = minSide * RIVER_WIDTHS[river] * breathe * 0.5;
    }

    glowContext.setTransform(glowCanvas.width / width, 0, 0, glowCanvas.height / height, 0, 0);
    glowContext.clearRect(0, 0, width, height);
    glowContext.globalCompositeOperation = "lighter";
    glowContext.imageSmoothingEnabled = false;

    for (let spriteIndex = 0; spriteIndex < 16; spriteIndex += 1) {
      const category = Math.floor(spriteIndex / 4);
      const sizeMin = SIZE_MIN[category];
      const sizeRange = SIZE_RANGE[category];
      for (let order = bucketOffsets[spriteIndex]; order < bucketOffsets[spriteIndex + 1]; order += 1) {
        const particleIndex = particleOrder[order];
        const offset = particleIndex * PARTICLE_STRIDE;
        const river = particles[offset + 5];
        const phase = particles[offset + 3];
        const period = particles[offset + 7];
        const size = sizeMin + particles[offset + 9] * sizeRange;
        let x;
        let y;
        let alpha;

        if (river < 0) {
          x = particles[offset + 9] * width;
          y = particles[offset + 10] * height;
          alpha = 0.1 + 0.12 * (0.5 + 0.5 * Math.sin(elapsed * TAU / period + phase));
        } else {
          const breathe = 1 + Math.sin(elapsed * TAU / RIVER_BREATHE_PERIODS[river] +
            RIVER_WAVE_PHASES[river]) * 0.15;
          const direction = RIVER_DIRECTIONS[river];
          const speed = particles[offset + 2];
          const baseAngle = particles[offset] + direction * TAU / RIVER_PERIODS[river] *
            speed * elapsed + Math.sin(elapsed * 0.025 + phase) * 0.012 +
            Math.sin(particles[offset] * 2 - elapsed * 0.045 +
              RIVER_WAVE_PHASES[river]) * 0.04;
          const radial = particles[offset + 1];
          const angle = baseAngle + Math.sin(elapsed * 0.013 + phase) * 0.003;
          const flowNoise =
            1 +
            Math.sin(angle * 2 + elapsed * 0.017 + RIVER_WAVE_PHASES[river]) * 0.18 +
            Math.sin(angle * 5 - elapsed * 0.011 + phase * 0.7) * 0.12 +
            Math.sin(angle * 9 + elapsed * 0.009 + river * 1.3) * 0.07;
          const strand = (particles[offset + 6] - 1) * riverHalfWidths[river] * 0.22 *
            Math.max(0.45, flowNoise);
          riverPoint(angle, river, elapsed, centerX, centerY,
            radial * riverHalfWidths[river] * breathe * flowNoise + strand);
          x = pointScratch[0];
          y = pointScratch[1];
          const distanceFactor = Math.max(0, 1 - Math.abs(radial) / 1.25);
          const twinkle = 0.82 + 0.18 * (0.5 + 0.5 * Math.sin(elapsed * TAU / period + phase));
          alpha = Math.max(0.25,
            (0.25 + distanceFactor * 0.75) * twinkle * particles[offset + 8]);
          if (river === 2) alpha *= 0.78;

          const highlightAngle = (elapsed * TAU / HIGHLIGHT_PERIODS[river] +
            RIVER_WAVE_PHASES[river]) % TAU;
          let highlightDistance = Math.abs(angle - highlightAngle);
          if (highlightDistance > Math.PI) highlightDistance = TAU - highlightDistance;
          const highlight = Math.max(0, 1 - highlightDistance / 0.17);
          alpha = Math.min(1, alpha + highlight * 0.45);

          if (category === 3 && particles[offset + 11] > 0) {
            const flash = Math.max(0, Math.sin(elapsed * (1.3 + particles[offset + 2]) + phase));
            alpha = Math.min(1, alpha * (0.55 + flash * flash * 0.75));
          }
        }

        const drawSize = size;
        const includeGlow = category >= 1
          ? (!weakGlow || particleIndex % 2 === 0)
          : (!weakGlow ? particleIndex % 3 === 0 : particleIndex % 6 === 0);
        if (river >= 0 && includeGlow) {
          const glowSize = category === 0 ? 18 : category === 1 ? 24 : category === 2 ? 36 : 48;
          glowContext.globalAlpha = Math.max(0.16, Math.min(0.62, alpha * 0.48));
          glowContext.drawImage(
            glowSprites[category],
            x - glowSize / 2,
            y - glowSize / 2,
            glowSize,
            glowSize
          );
        }
        context.globalAlpha = Math.max(0.08, Math.min(1, alpha));
        context.drawImage(sprites[spriteIndex], x - drawSize / 2, y - drawSize / 2,
          drawSize, drawSize);
      }
    }
    glowContext.globalAlpha = 1;
    glowContext.globalCompositeOperation = "source-over";

    wideContext.setTransform(wideCanvas.width / width, 0, 0, wideCanvas.height / height, 0, 0);
    wideContext.clearRect(0, 0, width, height);
    if (wideGlowEnabled) {
      wideContext.imageSmoothingEnabled = true;
      wideContext.imageSmoothingQuality = "high";
      wideContext.globalCompositeOperation = "lighter";
      wideContext.globalAlpha = 1;
      wideContext.drawImage(glowCanvas, 0, 0, width, height);
      wideContext.globalCompositeOperation = "source-over";
    }

    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.globalCompositeOperation = "lighter";
    context.globalAlpha = narrowGlowEnabled ? 0.55 : 0.42;
    context.drawImage(glowCanvas, 0, 0, width, height);
    if (wideGlowEnabled) {
      context.globalAlpha = 0.35;
      context.drawImage(wideCanvas, 0, 0, width, height);
    }
    context.globalAlpha = 1;
    context.globalCompositeOperation = "source-over";
  }

  function updateToggle() {
    if (!toggle) return;
    const action = enabled ? "вимкнути" : "увімкнути";
    const label = "Золоті частинки: " + action;
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("aria-pressed", String(enabled));
    toggle.dataset.tooltip = label;
  }

  function pauseLoop() {
    if (frame) window.cancelAnimationFrame(frame);
    frame = 0;
  }

  function stopLoop() {
    loopAllowed = false;
    pauseLoop();
    context.clearRect(0, 0, width, height);
  }

  function tick(timestamp) {
    frame = 0;
    if (!loopAllowed || document.hidden) return;
    const started = performance.now();
    drawFrame(Date.now() / 1000);
    frame = window.requestAnimationFrame(tick);

    const duration = performance.now() - started;
    if (duration > 20 && adaptiveLevel > 0.5) {
      slowFrames += 1;
      if (slowFrames >= 3) {
        adaptiveLevel = Math.max(0.5, adaptiveLevel * 0.9);
        slowFrames = 0;
        buildParticles();
      }
    } else {
      slowFrames = 0;
    }
  }

  function startLoop() {
    if (!loopAllowed || document.hidden || frame) return;
    frame = window.requestAnimationFrame(tick);
  }

  function setEnabled(next, persist) {
    if (enabled === next) return;
    enabled = next;
    if (persist) persistState();
    updateToggle();
    window.clearTimeout(stopTimer);
    if (enabled) {
      loopAllowed = true;
      canvas.classList.add("is-visible");
      startLoop();
    } else {
      canvas.classList.remove("is-visible");
      stopTimer = window.setTimeout(stopLoop, reducedMotion.matches ? 120 : 500);
    }
  }

  function createToggle() {
    if (toggle) return toggle;
    toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "navbar-particles-toggle";
    toggle.setAttribute("data-particles-toggle", "");
    toggle.innerHTML = '<img src="/static/svg/sparkle-toggle.svg" alt="" aria-hidden="true">';
    toggle.addEventListener("click", () => setEnabled(!enabled, true));
    updateToggle();
    if (window.MSHeaderTooltip) {
      window.MSHeaderTooltip.attach(toggle);
    }
    return toggle;
  }

  function mountToggle() {
    const container = document.querySelector(".navbar .container");
    if (!container) return false;
    const button = createToggle();
    const brand = container.querySelector(".navbar-brand");
    if (!brand) return false;
    if (button.parentElement !== container || button.previousElementSibling !== brand) {
      container.insertBefore(button, brand.nextSibling);
    }
    if (toggleObserver) {
      toggleObserver.disconnect();
      toggleObserver = null;
    }
    return true;
  }

  function initToggle() {
    if (mountToggle()) return;
    toggleObserver = new MutationObserver(mountToggle);
    const container = document.querySelector(".navbar .container");
    if (container) toggleObserver.observe(container, { childList: true });
  }

  function initialize() {
    buildSprites();
    resizeCanvas();
    initToggle();
    window.addEventListener("resize", resizeCanvas, { passive: true });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) pauseLoop();
      else startLoop();
    });
    window.addEventListener("pagehide", pauseLoop);
    window.addEventListener("pageshow", startLoop);
    loopAllowed = enabled;
    if (enabled) {
      canvas.classList.add("is-visible");
      startLoop();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
