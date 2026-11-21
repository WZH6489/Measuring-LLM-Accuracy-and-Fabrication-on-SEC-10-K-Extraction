"""Liquid-glass refraction for FilingCheck.

Streamlit cannot run scripts in the page directly, so a tiny hidden iframe injects
GLASS_JS into the parent document once. From then on it watches the page and gives
every glass surface (elements with class `fc-lg`, plus a few Streamlit widgets) a
per-size SVG displacement filter through `backdrop-filter: url(#...)`.

The refraction field is a port of the fragment shader in liquid-glass-js
(https://github.com/dashersw/liquid-glass-js), Copyright (c) 2025 Armagan Amcalar,
MIT. Permission is hereby granted, free of charge, to any person obtaining a copy of
that software to deal in it without restriction, provided this notice travels with
it; it is provided "as is", without warranty of any kind. Keep this header on any copy.

SVG-referenced backdrop filters only render in Chromium. Safari parses them and paints
nothing, so the script gates on the browser engine and does nothing elsewhere: the
stylesheet's plain `backdrop-filter: blur(...)` fallback stays in effect.
"""

# Elements that get the full refraction effect. Chips and small repeated items stay CSS-only.
SELECTOR = ('.fc-lg, [role="tablist"], [data-testid="stTextInputRootElement"], '
            '[data-testid="stExpander"] details')

GLASS_JS = r"""
(function () {
  const W = window;
  if (W.__fcGlass) { W.__fcGlass.scan(); return; }
  const brands = navigator.userAgentData && navigator.userAgentData.brands;
  const ok = brands ? brands.some(b => /Chromium|Google Chrome|Microsoft Edge/i.test(b.brand))
                    : /Chrome\//.test(navigator.userAgent);
  if (!ok) { W.__fcGlass = { scan() {} }; return; }

  const SEL = __SELECTOR__;
  const CFG = { edgeIntensity: .015, rimIntensity: .028, baseIntensity: .05, edgeDistance: .5, rimDistance: 1.7,
                baseDistance: .2, cornerBoost: .06, rippleEffect: .26, blurRadius: 2, warp: false };
  const SUPERSAMPLE = 2, MAX_MAP_EDGE = 1400, BLUR_STD_PER_RADIUS = .35, NS = 'http://www.w3.org/2000/svg';
  const filters = new Map(), tracked = new Map();
  let defs = null, nextId = 0;

  const clampByte = v => Math.max(0, Math.min(255, Math.round(v)));
  const resolveRadius = (c, w, h) => { const v = parseFloat(c) || 0; return c.trim().endsWith('%') ? v / 100 * Math.min(w, h) : v; };

  function buildMap(w, h, radius, pageW, pageH) {
    const ss = Math.max(.25, Math.min(SUPERSAMPLE, MAX_MAP_EDGE / Math.max(w, h)));
    const bw = Math.max(1, Math.round(w * ss)), bh = Math.max(1, Math.round(h * ss));
    const r = radius * ss, minDim = Math.min(w, h);
    const dx = new Float32Array(bw * bh), dy = new Float32Array(bw * bh);
    let maxAbs = 0;
    for (let py = 0; py < bh; py++) for (let px = 0; px < bw; px++) {
      const cx = (px + .5) / bw, cy = (py + .5) / bh;
      const tx = Math.abs(px + .5 - bw / 2) - (bw / 2 - r), ty = Math.abs(py + .5 - bh / 2) - (bh / 2 - r);
      const distPx = Math.max(-(Math.hypot(Math.max(tx, 0), Math.max(ty, 0)) + Math.min(Math.max(tx, ty), 0) - r), 0) / ss;
      const edgeFall = Math.exp(-distPx * CFG.edgeDistance), rimFall = Math.exp(-distPx * CFG.rimDistance);
      const base = CFG.warp ? (1 - Math.exp(-distPx * CFG.baseDistance)) * CFG.baseIntensity : 0;
      const total = base + edgeFall * CFG.edgeIntensity + rimFall * CFG.rimIntensity;
      let nx = cx - .5, ny = cy - .5; const len = Math.hypot(nx, ny); if (len > 0) { nx /= len; ny /= len; }
      const corner = Math.exp(-Math.max(Math.min(cx, 1 - cx), Math.min(cy, 1 - cy)) * minDim * .3) * CFG.cornerBoost;
      const ripple = Math.sin(distPx / minDim * 25) * CFG.rippleEffect * rimFall;
      const fx = (nx * (total + corner) - ny * ripple) * pageW, fy = (ny * (total + corner) + nx * ripple) * pageH;
      const i = py * bw + px; dx[i] = fx; dy[i] = fy;
      maxAbs = Math.max(maxAbs, Math.abs(fx), Math.abs(fy));
    }
    const scale = Math.max(maxAbs * 2, 1e-4), bias = scale * (128 / 255 - .5);
    const cv = document.createElement('canvas'); cv.width = bw; cv.height = bh;
    const ctx = cv.getContext('2d'), img = ctx.createImageData(bw, bh), d = img.data;
    for (let i = 0; i < bw * bh; i++) {
      d[i * 4] = clampByte(255 * (.5 + (dx[i] - bias) / scale));
      d[i * 4 + 1] = clampByte(255 * (.5 + (dy[i] - bias) / scale));
      d[i * 4 + 2] = 128; d[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return { url: cv.toDataURL('image/png'), scale };
  }

  function ensureDefs() {
    if (!defs || !defs.isConnected) {
      const svg = document.createElementNS(NS, 'svg');
      svg.setAttribute('width', '0'); svg.setAttribute('height', '0'); svg.setAttribute('aria-hidden', 'true');
      svg.style.position = 'fixed';
      svg.appendChild(document.createElementNS(NS, 'defs'));
      document.body.appendChild(svg); defs = svg;
    }
    return defs.querySelector('defs');
  }

  function acquire(key, w, h, radius, pageW, pageH) {
    let e = filters.get(key);
    if (e && e.node.isConnected) { e.refs++; return e; }
    const { url, scale } = buildMap(w, h, radius, pageW, pageH);
    const id = 'fc-glass-' + nextId++, f = document.createElementNS(NS, 'filter');
    const m = scale / 2 + 3 * CFG.blurRadius * BLUR_STD_PER_RADIUS, mx = m / w * 100, my = m / h * 100;
    f.setAttribute('id', id); f.setAttribute('x', -mx + '%'); f.setAttribute('y', -my + '%');
    f.setAttribute('width', 100 + 2 * mx + '%'); f.setAttribute('height', 100 + 2 * my + '%');
    f.setAttribute('color-interpolation-filters', 'sRGB');
    const im = document.createElementNS(NS, 'feImage');
    for (const [k, v] of [['href', url], ['x', 0], ['y', 0], ['width', w], ['height', h], ['preserveAspectRatio', 'none'], ['result', 'map']]) im.setAttribute(k, v);
    const dm = document.createElementNS(NS, 'feDisplacementMap');
    for (const [k, v] of [['in', 'SourceGraphic'], ['in2', 'map'], ['scale', scale], ['xChannelSelector', 'R'], ['yChannelSelector', 'G'], ['result', 'displaced']]) dm.setAttribute(k, v);
    const bl = document.createElementNS(NS, 'feGaussianBlur');
    bl.setAttribute('in', 'displaced'); bl.setAttribute('stdDeviation', CFG.blurRadius * BLUR_STD_PER_RADIUS * 3); bl.setAttribute('result', 'frost');
    const sat = document.createElementNS(NS, 'feColorMatrix');
    sat.setAttribute('in', 'frost'); sat.setAttribute('type', 'saturate'); sat.setAttribute('values', '1.35');
    f.append(im, dm, bl, sat); ensureDefs().appendChild(f);
    e = { id, node: f, refs: 1 }; filters.set(key, e); return e;
  }

  function release(key) {
    const e = key && filters.get(key);
    if (e && --e.refs <= 0) { filters.delete(key); e.node.remove(); }
  }

  function rebuild(el, st) {
    st.frame = 0;
    if (!el.isConnected) return;
    const w = Math.round(el.offsetWidth), h = Math.round(el.offsetHeight);
    if (w < 2 || h < 2) return;
    const radius = Math.min(resolveRadius(getComputedStyle(el).borderTopLeftRadius, w, h), Math.min(w, h) / 2);
    const key = [w, h, radius, innerWidth, innerHeight].join('|');
    if (key === st.key && el.style.backdropFilter) return;
    release(st.key);
    st.key = key;
    el.style.backdropFilter = 'url(#' + acquire(key, w, h, radius, innerWidth, innerHeight).id + ')';
  }

  function schedule(el) {
    const st = tracked.get(el); if (!st) return;
    cancelAnimationFrame(st.frame); st.frame = requestAnimationFrame(() => rebuild(el, st));
  }

  const ro = new ResizeObserver(entries => entries.forEach(e => schedule(e.target)));

  function scan() {
    for (const [el, st] of tracked) if (!el.isConnected) { ro.unobserve(el); release(st.key); tracked.delete(el); }
    document.querySelectorAll(SEL).forEach(el => {
      if (!tracked.has(el)) { tracked.set(el, { key: null, frame: 0 }); ro.observe(el); }
      schedule(el);
    });
  }

  let pending = 0, resizeT;
  new MutationObserver(() => { cancelAnimationFrame(pending); pending = requestAnimationFrame(scan); })
    .observe(document.body, { childList: true, subtree: true });
  addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(() => tracked.forEach((_, el) => schedule(el)), 180); }, { passive: true });
  W.__fcGlass = { scan };
  scan();
})();
"""


def loader():
    """HTML for a hidden iframe that installs GLASS_JS into the parent page (once per browser tab)."""
    import json
    code = GLASS_JS.replace("__SELECTOR__", json.dumps(SELECTOR))
    return ("<script>const d = window.parent.document;"
            "if (!d.getElementById('fc-glass-js')) { const s = d.createElement('script'); s.id = 'fc-glass-js';"
            f"s.textContent = {json.dumps(code)}; d.head.appendChild(s); }}"
            "else if (window.parent.__fcGlass) { window.parent.__fcGlass.scan(); }</script>")
