// Baut die PWA nach ../backend/static (dort liefert der Server sie aus).
import * as esbuild from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const out = process.env.OUT_DIR || "../backend/static";
const watch = process.argv.includes("--watch");
rmSync(out, { recursive: true, force: true });
mkdirSync(`${out}/assets`, { recursive: true });
cpSync("public", out, { recursive: true });
cpSync("index.html", `${out}/index.html`);

const options = {
  entryPoints: { main: "src/main.tsx" },
  bundle: true,
  outdir: `${out}/assets`,
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
if (watch) (await esbuild.context(options)).watch();
else await esbuild.build(options);
