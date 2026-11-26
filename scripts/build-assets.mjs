import { copyFileSync, existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");

const docsDir = path.join(rootDir, "docs");
const vendorDir = path.join(docsDir, "vendor");
const cssDir = path.join(docsDir, "css");
const inputCss = path.join(rootDir, "src", "input.css");
const outputCss = path.join(cssDir, "styles.css");
const indexHtmlPath = path.join(docsDir, "index.html");
const appJsPath = path.join(docsDir, "js", "app.js");

if (existsSync(path.join(docsDir, "node_modules"))) {
  throw new Error("docs/node_modules must not exist. Keep node_modules at repo root only.");
}

mkdirSync(vendorDir, { recursive: true });
mkdirSync(cssDir, { recursive: true });

// 1. 複製本地 Chart.js UMD bundle 至 docs/vendor/
const chartCandidates = [
  path.join(rootDir, "node_modules", "chart.js", "dist", "chart.umd.min.js"),
  path.join(rootDir, "node_modules", "chart.js", "dist", "chart.umd.js"),
];
const chartSrc = chartCandidates.find((p) => existsSync(p));
if (!chartSrc) {
  throw new Error("Chart.js dist bundle not found in node_modules. Run `npm install` or `npm ci` first.");
}
const chartDest = path.join(vendorDir, "chart.umd.min.js");
copyFileSync(chartSrc, chartDest);

// 2. 使用本地 @tailwindcss/cli 產出精簡 CSS
const tailwindCliJs = path.join(rootDir, "node_modules", "@tailwindcss", "cli", "dist", "index.mjs");
const tailwindBin = path.join(
  rootDir,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "tailwindcss.cmd" : "tailwindcss"
);

if (existsSync(tailwindCliJs)) {
  execFileSync(process.execPath, [tailwindCliJs, "-i", inputCss, "-o", outputCss, "--minify"], {
    cwd: rootDir,
    stdio: "inherit",
  });
} else if (existsSync(tailwindBin)) {
  execFileSync(tailwindBin, ["-i", inputCss, "-o", outputCss, "--minify"], {
    cwd: rootDir,
    stdio: "inherit",
  });
} else {
  throw new Error("@tailwindcss/cli not found in node_modules. Run `npm install` or `npm ci` first.");
}

// 3. 驗證輸出檔案與零外部 CDN 政策
for (const assetPath of [outputCss, chartDest, appJsPath, indexHtmlPath]) {
  if (!existsSync(assetPath) || statSync(assetPath).size === 0) {
    throw new Error(`Missing or empty built asset: ${path.relative(rootDir, assetPath)}`);
  }
}

const html = readFileSync(indexHtmlPath, "utf8");
const forbiddenPatterns = [
  /cdn\.tailwindcss\.com/i,
  /cdn\.jsdelivr\.net/i,
  /unpkg\.com/i,
  /cdnjs\.cloudflare\.com/i,
  /<script[^>]+src=["']https?:\/\//i,
  /<link[^>]+href=["']https?:\/\//i,
];
for (const pattern of forbiddenPatterns) {
  if (pattern.test(html)) {
    throw new Error(`Forbidden external CDN reference detected in docs/index.html: ${pattern}`);
  }
}

console.log("Build verified:", {
  css: path.relative(rootDir, outputCss),
  chartjs: path.relative(rootDir, chartDest),
  appJs: path.relative(rootDir, appJsPath),
});