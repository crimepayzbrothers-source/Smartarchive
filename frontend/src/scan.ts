// Dokument-Scanner im Browser: Kanten finden, perspektivisch entzerren, für die Texterkennung aufbereiten.
// Läuft komplett auf dem Gerät; es wird nichts hochgeladen, bevor Sie „Fertig“ tippen.

export type Pt = { x: number; y: number };
export type Quad = [Pt, Pt, Pt, Pt]; // oben links, oben rechts, unten rechts, unten links
export type Filter = "dokument" | "farbe" | "original";

export async function loadBitmap(file: Blob): Promise<ImageBitmap> {
  try {
    return await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return await createImageBitmap(file);
  }
}

function canvas(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(w));
  c.height = Math.max(1, Math.round(h));
  return c;
}

/** Bild verkleinert als Pixeldaten (lange Seite höchstens maxSide). */
function pixels(src: CanvasImageSource & { width: number; height: number }, maxSide: number) {
  const scale = Math.min(1, maxSide / Math.max(src.width, src.height));
  const c = canvas(src.width * scale, src.height * scale);
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(src, 0, 0, c.width, c.height);
  return { data: ctx.getImageData(0, 0, c.width, c.height), scale };
}

export function insetQuad(w: number, h: number, f = 0.06): Quad {
  return [{ x: w * f, y: h * f }, { x: w * (1 - f), y: h * f }, { x: w * (1 - f), y: h * (1 - f) }, { x: w * f, y: h * (1 - f) }];
}

function boxBlur(src: Float32Array, w: number, h: number, r: number) {
  const tmp = new Float32Array(src.length);
  const out = new Float32Array(src.length);
  for (let y = 0; y < h; y++) {
    let acc = 0;
    for (let x = -r; x <= r; x++) acc += src[y * w + Math.min(w - 1, Math.max(0, x))];
    for (let x = 0; x < w; x++) {
      tmp[y * w + x] = acc / (2 * r + 1);
      acc += src[y * w + Math.min(w - 1, x + r + 1)] - src[y * w + Math.max(0, x - r)];
    }
  }
  for (let x = 0; x < w; x++) {
    let acc = 0;
    for (let y = -r; y <= r; y++) acc += tmp[Math.min(h - 1, Math.max(0, y)) * w + x];
    for (let y = 0; y < h; y++) {
      out[y * w + x] = acc / (2 * r + 1);
      acc += tmp[Math.min(h - 1, y + r + 1) * w + x] - tmp[Math.max(0, y - r) * w + x];
    }
  }
  return out;
}

function otsu(gray: Float32Array) {
  const hist = new Array(256).fill(0);
  for (const v of gray) hist[Math.max(0, Math.min(255, v | 0))]++;
  const total = gray.length;
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * hist[i];
  let sumB = 0, wB = 0, best = 0, thr = 128;
  for (let t = 0; t < 256; t++) {
    wB += hist[t];
    if (!wB) continue;
    const wF = total - wB;
    if (!wF) break;
    sumB += t * hist[t];
    const mB = sumB / wB, mF = (sum - sumB) / wF;
    const between = wB * wF * (mB - mF) ** 2;
    if (between > best) { best = between; thr = t; }
  }
  return thr;
}

function polyArea(q: Pt[]) {
  let a = 0;
  for (let i = 0; i < q.length; i++) {
    const p = q[i], n = q[(i + 1) % q.length];
    a += p.x * n.y - n.x * p.y;
  }
  return Math.abs(a) / 2;
}

/**
 * Sucht das Blatt: Es ist meist die größte helle, zusammenhängende Fläche.
 * Ecken = Extrempunkte dieser Fläche. Ohne klares Ergebnis: leicht eingerückter Rahmen.
 */
export function detectQuad(bmp: ImageBitmap): { quad: Quad; found: boolean } {
  const { data, scale } = pixels(bmp, 360);
  const w = data.width, h = data.height, px = data.data;
  const gray = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) gray[i] = 0.299 * px[i * 4] + 0.587 * px[i * 4 + 1] + 0.114 * px[i * 4 + 2];
  const blur = boxBlur(gray, w, h, 2);
  const thr = otsu(blur);
  const fallback = { quad: insetQuad(bmp.width, bmp.height), found: false };

  // größte helle Fläche (4er-Nachbarschaft)
  const label = new Int32Array(w * h).fill(-1);
  const stack = new Int32Array(w * h);
  let bestId = -1, bestSize = 0, bestBorder = 0;
  let id = 0;
  for (let start = 0; start < w * h; start++) {
    if (label[start] !== -1 || blur[start] <= thr) continue;
    let sp = 0, size = 0, border = 0;
    stack[sp++] = start;
    label[start] = id;
    while (sp) {
      const i = stack[--sp];
      size++;
      const x = i % w, y = (i / w) | 0;
      if (x === 0 || y === 0 || x === w - 1 || y === h - 1) border++;
      const nb = [x > 0 ? i - 1 : -1, x < w - 1 ? i + 1 : -1, y > 0 ? i - w : -1, y < h - 1 ? i + w : -1];
      for (const n of nb) {
        if (n >= 0 && label[n] === -1 && blur[n] > thr) { label[n] = id; stack[sp++] = n; }
      }
    }
    if (size > bestSize) { bestSize = size; bestId = id; bestBorder = border; }
    id++;
  }
  if (bestId < 0 || bestSize < 0.12 * w * h || bestSize > 0.97 * w * h) return fallback;
  // Fläche läuft rundum über den Bildrand -> vermutlich heller Hintergrund statt Blatt
  if (bestBorder > 1.2 * (w + h)) return fallback;

  let tl = { x: 0, y: 0, v: Infinity }, br = { x: 0, y: 0, v: -Infinity };
  let tr = { x: 0, y: 0, v: -Infinity }, bl = { x: 0, y: 0, v: Infinity };
  for (let i = 0; i < w * h; i++) {
    if (label[i] !== bestId) continue;
    const x = i % w, y = (i / w) | 0;
    if (x + y < tl.v) tl = { x, y, v: x + y };
    if (x + y > br.v) br = { x, y, v: x + y };
    if (x - y > tr.v) tr = { x, y, v: x - y };
    if (x - y < bl.v) bl = { x, y, v: x - y };
  }
  const q: Quad = [tl, tr, br, bl].map((p) => ({ x: p.x / scale, y: p.y / scale })) as Quad;
  const area = polyArea(q) * scale * scale;
  if (area < 0.6 * bestSize || area < 0.1 * w * h) return fallback; // keine viereckige Form
  return { quad: q, found: true };
}

/** Homografie, die das Rechteck (0,0)-(W,H) auf das Viereck q abbildet. */
function homography(W: number, H: number, q: Quad) {
  const src = [[0, 0], [W, 0], [W, H], [0, H]];
  const A: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i];
    const { x: u, y: v } = q[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
  }
  // Gauß-Elimination
  for (let c = 0; c < 8; c++) {
    let p = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]];
    [b[c], b[p]] = [b[p], b[c]];
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const f = A[r][c] / A[c][c];
      for (let k = c; k < 8; k++) A[r][k] -= f * A[c][k];
      b[r] -= f * b[c];
    }
  }
  return b.map((v, i) => v / A[i][i]);
}

const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y);

/** Schneidet das Viereck aus und zieht es gerade. Ergebnis: Canvas in Zielgröße. */
export function warp(bmp: ImageBitmap, quad: Quad, maxSide = 2400): HTMLCanvasElement {
  const { data, scale } = pixels(bmp, 3200);
  const q = quad.map((p) => ({ x: p.x * scale, y: p.y * scale })) as Quad;
  let W = Math.max(dist(q[0], q[1]), dist(q[3], q[2]));
  let H = Math.max(dist(q[0], q[3]), dist(q[1], q[2]));
  const f = Math.min(1, maxSide / Math.max(W, H));
  W = Math.max(1, Math.round(W * f));
  H = Math.max(1, Math.round(H * f));
  const [a, b, c, d, e, g, h1, h2] = homography(W, H, q);
  const sw = data.width, sh = data.height, sp = data.data;
  const out = canvas(W, H);
  const ctx = out.getContext("2d")!;
  const img = ctx.createImageData(W, H);
  const op = img.data;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const den = h1 * x + h2 * y + 1;
      const u = (a * x + b * y + c) / den;
      const v = (d * x + e * y + g) / den;
      const o = (y * W + x) * 4;
      if (u < 0 || v < 0 || u > sw - 1 || v > sh - 1) { op[o] = op[o + 1] = op[o + 2] = 255; op[o + 3] = 255; continue; }
      const x0 = u | 0, y0 = v | 0, x1 = Math.min(sw - 1, x0 + 1), y1 = Math.min(sh - 1, y0 + 1);
      const fx = u - x0, fy = v - y0;
      const i00 = (y0 * sw + x0) * 4, i10 = (y0 * sw + x1) * 4, i01 = (y1 * sw + x0) * 4, i11 = (y1 * sw + x1) * 4;
      for (let k = 0; k < 3; k++) {
        const top = sp[i00 + k] + (sp[i10 + k] - sp[i00 + k]) * fx;
        const bot = sp[i01 + k] + (sp[i11 + k] - sp[i01 + k]) * fx;
        op[o + k] = top + (bot - top) * fy;
      }
      op[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return out;
}

/** Bildverbesserung: „Dokument“ = Graustufen mit ausgeglichenem Hintergrund (gut lesbar, gut für OCR). */
export function applyFilter(src: HTMLCanvasElement, mode: Filter): HTMLCanvasElement {
  if (mode === "original") return src;
  const w = src.width, h = src.height;
  const out = canvas(w, h);
  const ctx = out.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(src, 0, 0);
  const img = ctx.getImageData(0, 0, w, h);
  const p = img.data;
  if (mode === "farbe") {
    // Tonwertkorrektur je Kanal (1 % / 99 %)
    for (let k = 0; k < 3; k++) {
      const hist = new Array(256).fill(0);
      for (let i = k; i < p.length; i += 4) hist[p[i]]++;
      const n = w * h;
      let lo = 0, hi = 255, acc = 0;
      while (lo < 255 && (acc += hist[lo]) < n * 0.01) lo++;
      acc = 0;
      while (hi > 0 && (acc += hist[hi]) < n * 0.01) hi--;
      const span = Math.max(1, hi - lo);
      for (let i = k; i < p.length; i += 4) p[i] = Math.max(0, Math.min(255, ((p[i] - lo) * 255) / span));
    }
  } else {
    // Hintergrund schätzen (Maximum in Blöcken, geglättet) und herausrechnen
    const bs = Math.max(8, Math.round(Math.max(w, h) / 48));
    const bw = Math.ceil(w / bs), bh = Math.ceil(h / bs);
    const bg = new Float32Array(bw * bh);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        const v = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
        const bi = ((y / bs) | 0) * bw + ((x / bs) | 0);
        if (v > bg[bi]) bg[bi] = v;
      }
    }
    const bgs = boxBlur(bg, bw, bh, 1);
    for (let y = 0; y < h; y++) {
      const fy = Math.min(bh - 1.001, Math.max(0, y / bs - 0.5));
      const y0 = fy | 0, ty = fy - y0;
      for (let x = 0; x < w; x++) {
        const fx = Math.min(bw - 1.001, Math.max(0, x / bs - 0.5));
        const x0 = fx | 0, tx = fx - x0;
        const x1 = Math.min(bw - 1, x0 + 1), y1 = Math.min(bh - 1, y0 + 1);
        const b0 = bgs[y0 * bw + x0] + (bgs[y0 * bw + x1] - bgs[y0 * bw + x0]) * tx;
        const b1 = bgs[y1 * bw + x0] + (bgs[y1 * bw + x1] - bgs[y1 * bw + x0]) * tx;
        const back = Math.max(40, b0 + (b1 - b0) * ty);
        const i = (y * w + x) * 4;
        const v = (0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2]) / back;
        const o = Math.max(0, Math.min(255, ((v - 0.35) / 0.55) * 255));
        p[i] = p[i + 1] = p[i + 2] = o;
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  return out;
}

export function rotate(src: HTMLCanvasElement, quarterTurns: number): HTMLCanvasElement {
  const t = ((quarterTurns % 4) + 4) % 4;
  if (!t) return src;
  const out = canvas(t % 2 ? src.height : src.width, t % 2 ? src.width : src.height);
  const ctx = out.getContext("2d")!;
  ctx.translate(out.width / 2, out.height / 2);
  ctx.rotate((t * Math.PI) / 2);
  ctx.drawImage(src, -src.width / 2, -src.height / 2);
  return out;
}

export function toJpeg(c: HTMLCanvasElement, name: string, quality = 0.88): Promise<File> {
  return new Promise((resolve, reject) =>
    c.toBlob((b) => (b ? resolve(new File([b], name, { type: "image/jpeg" })) : reject(new Error("Bild konnte nicht erzeugt werden."))),
      "image/jpeg", quality));
}
