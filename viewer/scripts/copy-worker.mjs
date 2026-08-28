import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "node_modules", "@thatopen", "fragments", "dist", "Worker", "worker.mjs");
const destDir = join(root, "public");
mkdirSync(destDir, { recursive: true });
copyFileSync(src, join(destDir, "worker.mjs"));
