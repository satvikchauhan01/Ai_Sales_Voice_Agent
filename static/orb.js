// Voice orb: an animated sphere that shows the call state and moves with the audio.
//   idle      soft sunrise colours, slow breathing
//   listening green, pulses with YOUR microphone level
//   thinking  amber, shimmer + orbiting dots
//   speaking  orange, pulses with PRIYA's voice level
//   ended     grey
// Usage:  const orb = createOrb(canvas);  orb.setMode("listening");  orb.setLevelProvider(() => 0..1);

(function () {
  // [glow/body colour, edge colour, highlight colour] as RGB
  const PALETTES = {
    idle:      [[255, 190, 105], [242, 128, 40], [255, 240, 214]],
    listening: [[64, 214, 160], [14, 150, 115], [214, 250, 234]],
    thinking:  [[255, 205, 95], [214, 145, 20], [255, 246, 220]],
    speaking:  [[255, 160, 80], [232, 92, 30], [255, 232, 208]],
    ended:     [[196, 204, 218], [130, 141, 160], [238, 241, 247]],
  };

  function rgba(c, a) {
    return `rgba(${c[0] | 0}, ${c[1] | 0}, ${c[2] | 0}, ${a})`;
  }

  function createOrb(canvas) {
    const ctx = canvas.getContext("2d");
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let mode = "idle";
    let level = 0;        // smoothed level actually drawn
    let target = 0;       // latest level from the provider
    let provider = null;  // function returning 0..1
    let colors = PALETTES.idle.map((c) => c.slice());
    let t = 0;
    let width = 0;
    let height = 0;

    function resize() {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    if (window.ResizeObserver) {
      new ResizeObserver(resize).observe(canvas);
    }
    resize();

    // A circle whose edge wobbles gently (sum of sine waves).
    function blobPath(cx, cy, r, phase, wobble) {
      const points = 72;
      ctx.beginPath();
      for (let i = 0; i <= points; i++) {
        const a = (i / points) * Math.PI * 2;
        const n = Math.sin(a * 3 + phase) * 0.5 + Math.sin(a * 5 - phase * 1.3) * 0.3 + Math.sin(a * 2 + phase * 0.7) * 0.2;
        const rr = r * (1 + wobble * n);
        const x = cx + Math.cos(a) * rr;
        const y = cy + Math.sin(a) * rr;
        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.closePath();
    }

    function draw() {
      requestAnimationFrame(draw);
      if (!width || !height) {
        return;
      }
      t += reduceMotion ? 0.004 : 0.016;

      if (provider) {
        target = Math.max(0, Math.min(1, provider() || 0));
      }
      // rise fast, fall slowly - feels like a real VU meter
      level += (target - level) * (target > level ? 0.35 : 0.07);

      // fade colours towards the current mode's palette
      const palette = PALETTES[mode] || PALETTES.idle;
      for (let k = 0; k < 3; k++) {
        for (let j = 0; j < 3; j++) {
          colors[k][j] += (palette[k][j] - colors[k][j]) * 0.06;
        }
      }

      const lv = reduceMotion ? level * 0.35 : level;
      const cx = width / 2;
      const cy = height / 2;
      const half = Math.min(width, height) / 2;   // everything must stay inside this radius
      const base = half * 0.52;
      let r = base * (1 + lv * 0.22);
      if (mode === "idle" || mode === "ended") {
        r *= 1 + Math.sin(t * 1.6) * 0.02;
      }
      if (mode === "thinking") {
        r *= 1 + Math.sin(t * 4) * 0.02;
      }

      ctx.clearRect(0, 0, width, height);

      // 1) soft outer glow
      const glow = ctx.createRadialGradient(cx, cy, r * 0.5, cx, cy, half);
      glow.addColorStop(0, rgba(colors[0], 0.38 + lv * 0.25));
      glow.addColorStop(1, rgba(colors[0], 0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(cx, cy, half, 0, Math.PI * 2);
      ctx.fill();

      // 2) ripple rings while someone is talking (grow from the sphere to the canvas edge)
      if (mode === "listening" || mode === "speaking") {
        for (let i = 0; i < 3; i++) {
          const p = (t * 0.45 + i / 3) % 1;
          ctx.strokeStyle = rgba(colors[1], (1 - p) * (0.12 + lv * 0.45));
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(cx, cy, r * 1.06 + p * (half - 2 - r * 1.06), 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      // 3) the sphere body
      const body = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.1, cx, cy, r * 1.08);
      body.addColorStop(0, rgba(colors[2], 1));
      body.addColorStop(0.5, rgba(colors[0], 1));
      body.addColorStop(1, rgba(colors[1], 1));
      ctx.fillStyle = body;
      blobPath(cx, cy, r, t * 1.2, 0.02 + lv * 0.1);
      ctx.fill();

      // 4) liquid light moving inside the sphere
      ctx.save();
      blobPath(cx, cy, r, t * 1.2, 0.02 + lv * 0.1);
      ctx.clip();
      for (let i = 0; i < 3; i++) {
        const a = t * (0.55 + i * 0.22) + i * 2.1;
        const ox = cx + Math.cos(a) * r * 0.42;
        const oy = cy + Math.sin(a * 1.15) * r * 0.36;
        const g = ctx.createRadialGradient(ox, oy, 0, ox, oy, r * (0.7 + lv * 0.2));
        g.addColorStop(0, rgba(i === 1 ? colors[1] : colors[2], 0.5));
        g.addColorStop(1, rgba(colors[0], 0));
        ctx.fillStyle = g;
        ctx.fillRect(cx - r * 1.3, cy - r * 1.3, r * 2.6, r * 2.6);
      }
      if (mode === "thinking") {
        const a = t * 2.6;
        const sweep = ctx.createLinearGradient(
          cx + Math.cos(a) * r, cy + Math.sin(a) * r,
          cx - Math.cos(a) * r, cy - Math.sin(a) * r
        );
        sweep.addColorStop(0, "rgba(255,255,255,0.55)");
        sweep.addColorStop(0.45, "rgba(255,255,255,0)");
        ctx.fillStyle = sweep;
        ctx.fillRect(cx - r * 1.3, cy - r * 1.3, r * 2.6, r * 2.6);
      }
      // glossy highlight
      const hl = ctx.createRadialGradient(cx - r * 0.38, cy - r * 0.48, 0, cx - r * 0.38, cy - r * 0.48, r * 0.6);
      hl.addColorStop(0, "rgba(255,255,255,0.8)");
      hl.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = hl;
      ctx.fillRect(cx - r * 1.3, cy - r * 1.3, r * 2.6, r * 2.6);
      ctx.restore();

      // 5) orbiting dots while thinking
      if (mode === "thinking") {
        for (let i = 0; i < 3; i++) {
          const a = t * 2.2 + (i * Math.PI * 2) / 3;
          ctx.fillStyle = rgba(colors[1], 0.9);
          ctx.beginPath();
          ctx.arc(cx + Math.cos(a) * r * 1.24, cy + Math.sin(a) * r * 1.24, 4.5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
    requestAnimationFrame(draw);

    return {
      setMode(newMode) {
        mode = PALETTES[newMode] ? newMode : "idle";
      },
      setLevelProvider(fn) {
        provider = fn;
      },
    };
  }

  window.createOrb = createOrb;
})();
