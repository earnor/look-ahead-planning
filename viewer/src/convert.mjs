import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import * as FRAGS from "@thatopen/fragments";

function log(message) {
  // writeSync so Python/QProcess sees progress while stdout is piped.
  process.stdout.write(`${message}\n`);
}

const viewerRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ifcPath = resolve(process.argv[2] || "");
const outPath = resolve(process.argv[3] || "");

if (!ifcPath || !outPath) {
  log("Usage: node src/convert.mjs <input.ifc> <output.frag>");
  process.exit(1);
}
if (!existsSync(ifcPath)) {
  log(`IFC not found: ${ifcPath}`);
  process.exit(1);
}

const wasmDir = join(viewerRoot, "node_modules", "web-ifc");
if (!existsSync(join(wasmDir, "web-ifc.wasm"))) {
  log(`web-ifc WASM not found in ${wasmDir}. Run npm install in viewer/.`);
  process.exit(1);
}

const importer = new FRAGS.IfcImporter();
importer.wasm = {
  path: `${pathToFileURL(wasmDir).href}/`,
  absolute: true,
};

log(`Converting ${ifcPath}`);
const bytes = new Uint8Array(readFileSync(ifcPath));
const frag = await importer.process({
  bytes,
  progressCallback: (progress) => {
    const pct = Math.round((Number(progress) || 0) * 100);
    log(`PROGRESS ${pct}`);
  },
});

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, frag);
log(`Wrote ${outPath} (${frag.length} bytes)`);
