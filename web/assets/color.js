/* 印象色 → 界面用的成对颜色。原版 lib/imageColor.ts + theme/herVars.ts 的移植：
   同样的 OKLCH 推导、同样的对比度校验，所以壁纸/记号/按钮的颜色和原版一致。 */

const paper = { body: { light: "#fbfcfe", dark: "#14171d" }, ink: { light: "#1c2a4a", dark: "#e3e9f5" } };
const mutedColors = { light: "#f5f5f5", dark: "#262626" };
const shareTrack = "#e8e7e2";
const AA = 4.5;
const GRAPHIC = 3;

function normalizeHex(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
  if (!m) return "#000000";
  return "#" + m[1].toLowerCase();
}

function toRgb(hex) {
  const n = parseInt(normalizeHex(hex).slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

const toLinear = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const fromLinear = (v) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);

function luminance(hex) {
  const [r, g, b] = toRgb(hex).map(toLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function toOklch(hex) {
  const [r, g, b] = toRgb(hex).map(toLinear);
  const l_ = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m_ = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s_ = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_;
  const A = 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_;
  const B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_;
  return { l: L, c: Math.hypot(A, B), h: Math.atan2(B, A) };
}

function oklabToRgb(L, A, B) {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map(fromLinear);
}

function toHex(rgb) {
  return "#" + rgb.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0")).join("");
}

function fromOklch({ l, c, h }) {
  for (let chroma = c; chroma >= 0; chroma -= 0.002) {
    const rgb = oklabToRgb(l, chroma * Math.cos(h), chroma * Math.sin(h));
    if (rgb.every((v) => v >= -1e-4 && v <= 1 + 1e-4)) return toHex(rgb);
  }
  return toHex(oklabToRgb(l, 0, 0));
}

function darkenUntil(base, against, ratio, maxC) {
  for (let l = Math.min(base.l, 0.7); l > 0.05; l -= 0.01) {
    const hex = fromOklch({ l, c: Math.min(base.c, maxC), h: base.h });
    if (contrast(hex, against) >= ratio) return hex;
  }
  return "#000000";
}

function lightenUntil(base, against, ratio, maxC) {
  for (let l = Math.max(base.l, 0.5); l < 1; l += 0.01) {
    const hex = fromOklch({ l, c: Math.min(base.c, maxC), h: base.h });
    if (contrast(hex, against) >= ratio) return hex;
  }
  return "#ffffff";
}

function mostReadable(bg) {
  const candidates = [paper.body.light, paper.ink.light, paper.body.dark, paper.ink.dark];
  return candidates.reduce((best, c) => (contrast(c, bg) > contrast(best, bg) ? c : best));
}

function readableOn(base, against, ratio, maxC) {
  return luminance(against) > 0.18 ? darkenUntil(base, against, ratio, maxC) : lightenUntil(base, against, ratio, maxC);
}

function deriveScale(raw) {
  const base = toOklch(raw);
  const at = (l, c) => fromOklch({ l, c: Math.min(c, base.c), h: base.h });
  const wall = { light: at(0.955, 0.018), dark: at(0.2, 0.016) };
  const callDeep = at(0.26, base.c * 0.45);
  const mark = {
    light: contrast(raw, mutedColors.light) >= GRAPHIC ? raw : readableOn(base, mutedColors.light, GRAPHIC, 0.16),
    dark: contrast(raw, mutedColors.dark) >= GRAPHIC ? raw : readableOn(base, mutedColors.dark, GRAPHIC, 0.14),
  };
  const bar = mark;
  const buttonBg = { light: raw, dark: mark.dark };
  const accent = {
    light: readableOn(base, paper.body.light, AA, 0.16),
    dark: readableOn(base, paper.body.dark, AA, 0.14),
  };
  const buttonFg = {
    light: readableOn(base, buttonBg.light, AA, 0.14),
    dark: readableOn(base, buttonBg.dark, AA, 0.14),
  };
  return {
    raw,
    mark,
    bar,
    wall,
    button: { bg: buttonBg, fg: buttonFg },
    share: {
      banner: raw,
      onBanner: contrast(raw, "#ffffff") >= AA ? "#ffffff" : buttonFg.light,
      bar: contrast(raw, shareTrack) >= GRAPHIC ? raw : readableOn(base, shareTrack, GRAPHIC, 0.16),
    },
    accent,
    onAccent: { light: mostReadable(accent.light), dark: mostReadable(accent.dark) },
    call: { deep: callDeep },
  };
}

const scales = new Map();

export function imageColorScale(hex) {
  const raw = normalizeHex(hex);
  if (!scales.has(raw)) scales.set(raw, deriveScale(raw));
  return scales.get(raw);
}

/** 原版的 herVars()：把她的印象色注入一棵子树（CSS 自定义属性）。 */
export function herVars(color) {
  const s = imageColorScale(color);
  const ld = (p) => `light-dark(${p.light}, ${p.dark})`;
  const style = {
    "--primary": ld(s.accent),
    "--primary-foreground": ld(s.onAccent),
    "--ring": ld(s.mark),
    "--her-wall": ld(s.wall),
    "--her-mark": ld(s.mark),
    "--her-bar": ld(s.bar),
    "--her-button": ld(s.button.bg),
    "--her-button-fg": ld(s.button.fg),
    "--her-call-deep": s.call.deep,
  };
  return Object.entries(style)
    .map(([k, v]) => `${k}:${v}`)
    .join(";");
}
