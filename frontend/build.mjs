// Baut die PWA nach ../backend/static (dort liefert der Server sie aus).
import * as esbuild from "esbuild";
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";

const out = process.env.OUT_DIR || "../backend/static";
const watch = process.argv.includes("--watch");
rmSync(out, { recursive: true, force: true });
mkdirSync(`${out}/assets`, { recursive: true });
cpSync("public", out, { recursive: true });

const options = {
  entryPoints: { main: "src/main.tsx" },
  bundle: true,
  outdir: `${out}/assets`,
  // Dateiname mit Inhalts-Prüfsumme: Jede Version hat einen neuen Namen, alte Stände bleiben nicht im Browser hängen.
  entryNames: watch ? "[name]" : "[name]-[hash]",
  metafile: true,
  format: "esm",
  target: ["es2021", "chrome100", "safari15"],
  jsx: "automatic",
  minify: !watch,
  sourcemap: watch,
  legalComments: "none",
  nodePaths: process.env.NODE_PATH ? process.env.NODE_PATH.split(":") : [],
  define: { "process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production") },
  logLevel: "info",
};
function writeIndex(metafile) {
  const outputs = Object.keys(metafile.outputs).map((f) => f.split("/assets/")[1]).filter(Boolean);
  const js = outputs.find((f) => /^main(-[^.]+)?\.js$/.test(f));
  const css = outputs.find((f) => /^main(-[^.]+)?\.css$/.test(f));
  const html = readFileSync("index.html", "utf8")
    .replace("/assets/main.css", `/assets/${css}`)
    .replace("/assets/main.js", `/assets/${js}`);
  writeFileSync(`${out}/index.html`, html);
}

if (watch) {
  const ctx = await esbuild.context({ ...options, plugins: [{ name: "index", setup(b) { b.onEnd((r) => r.metafile && writeIndex(r.metafile)); } }] });
  await ctx.watch();
} else {
  const result = await esbuild.build(options);
  writeIndex(result.metafile);
}
