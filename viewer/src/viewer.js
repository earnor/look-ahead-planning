import * as THREE from "three";
import * as OBC from "@thatopen/components";
import * as OBF from "@thatopen/components-front";

const container = document.getElementById("container");
const status = document.getElementById("status");

function setStatus(text) {
  if (!status) return;
  status.textContent = text;
  status.style.display = text ? "block" : "none";
}

async function fetchJson(url, fallback) {
  try {
    const response = await fetch(url);
    if (!response.ok) return fallback;
    const data = await response.json();
    return data ?? fallback;
  } catch (error) {
    console.warn(`Could not load ${url}:`, error);
    return fallback;
  }
}

function asGuidMap(payload) {
  if (!payload || typeof payload !== "object") return {};
  if (payload.module_to_guids && typeof payload.module_to_guids === "object") {
    return payload.module_to_guids;
  }
  return payload;
}

function normalizeStatus(raw) {
  if (!raw) return "unknown";
  const s = String(raw).toLowerCase().trim();
  if (s === "producting" || s === "producing") return "producing";
  if (s === "transportating" || s === "transporting") return "transporting";
  if (s === "installing" || s === "completed") return "installing";
  if (s === "unknown" || s === "upcoming") return "unknown";
  return "unknown";
}

const components = new OBC.Components();
const worlds = components.get(OBC.Worlds);
const world = worlds.create();

world.scene = new OBC.SimpleScene(components);
world.scene.setup();
world.scene.three.background = new THREE.Color(0xf5f5f5);

world.renderer = new OBF.PostproductionRenderer(components, container);
world.camera = new OBC.OrthoPerspectiveCamera(components);

components.init();
components.get(OBC.Grids).create(world);
await world.camera.controls.setLookAt(20, 20, 20, 0, 0, 0);

const fetchedWorker = await fetch("./worker.mjs");
const workerBlob = await fetchedWorker.blob();
const workerFile = new File([workerBlob], "worker.mjs", {
  type: "text/javascript",
});
const workerUrl = URL.createObjectURL(workerFile);

const fragments = components.get(OBC.FragmentsManager);
fragments.init(workerUrl);

world.camera.controls.addEventListener("update", () => {
  fragments.core.update();
});

fragments.list.onItemSet.add(({ value: model }) => {
  model.useCamera(world.camera.three);
  world.scene.three.add(model.object);
  fragments.core.update(true);
});

fragments.core.models.materials.list.onItemSet.add(({ value: material }) => {
  if (!("isLodMaterial" in material && material.isLodMaterial)) {
    material.polygonOffset = true;
    material.polygonOffsetUnits = 1;
    material.polygonOffsetFactor = Math.random();
  }
});

try {
  const response = await fetch("./model.frag");
  if (!response.ok) {
    throw new Error(`Could not load model.frag (${response.status})`);
  }
  const buffer = await response.arrayBuffer();
  await fragments.core.load(buffer, { modelId: "model" });
} catch (error) {
  console.error(error);
  setStatus(error.message || String(error));
}

const moduleToGuids = asGuidMap(await fetchJson("./module_guids.json", {}));
const moduleStatusRaw = await fetchJson("./module_status.json", {});

const guidMapper =
  fragments.guidsToModelIdMap?.bind(fragments) ||
  fragments.core?.guidsToModelIdMap?.bind(fragments.core);

const moduleToModelIdMap = {};
if (guidMapper) {
  for (const [moduleId, guids] of Object.entries(moduleToGuids)) {
    if (!Array.isArray(guids) || guids.length === 0) continue;
    try {
      const modelIdMap = await guidMapper(guids);
      if (modelIdMap && Object.keys(modelIdMap).length > 0) {
        moduleToModelIdMap[moduleId] = modelIdMap;
      }
    } catch (error) {
      console.warn(`No fragments for module ${moduleId}:`, error);
    }
  }
}

const localIdToModule = {};
for (const [moduleId, modelIdMap] of Object.entries(moduleToModelIdMap)) {
  for (const [modelId, localIds] of Object.entries(modelIdMap)) {
    for (const localId of localIds) {
      localIdToModule[`${modelId}:${localId}`] = moduleId;
    }
  }
}

const moduleToStatus = {};
for (const [moduleId, rawStatus] of Object.entries(moduleStatusRaw || {})) {
  moduleToStatus[moduleId] = normalizeStatus(rawStatus);
}

try {
  const casters = components.get(OBC.Raycasters);
  const caster = casters.get(world);
  const highlighter = components.get(OBF.Highlighter);
  highlighter.setup({ world });

  highlighter.styles.set("producing", {
    color: new THREE.Color("#f39c12"),
    opacity: 1,
    transparent: false,
    renderedFaces: 0,
  });
  highlighter.styles.set("transporting", {
    color: new THREE.Color("#3498db"),
    opacity: 1,
    transparent: false,
    renderedFaces: 0,
  });
  highlighter.styles.set("installing", {
    color: new THREE.Color("#27ae60"),
    opacity: 1,
    transparent: false,
    renderedFaces: 0,
  });
  highlighter.styles.set("unknown", {
    color: new THREE.Color("#95a5a6"),
    opacity: 1,
    transparent: false,
    renderedFaces: 0,
  });
  highlighter.styles.set("select", {
    color: new THREE.Color("#e74c3c"),
    opacity: 1,
    transparent: false,
    renderedFaces: 1,
  });

  for (const [moduleId, modelIdMap] of Object.entries(moduleToModelIdMap)) {
    if (!(moduleId in moduleToStatus)) continue;
    const colorStatus = moduleToStatus[moduleId] || "unknown";
    try {
      await highlighter.highlightByID(colorStatus, modelIdMap, false);
    } catch (error) {
      console.warn(`Could not color module ${moduleId}:`, error);
    }
  }

  function createLegend() {
    const legend = document.createElement("div");
    legend.style.position = "absolute";
    legend.style.top = "20px";
    legend.style.right = "20px";
    legend.style.padding = "12px 16px";
    legend.style.background = "rgba(255, 255, 255, 0.95)";
    legend.style.border = "1px solid #ccc";
    legend.style.borderRadius = "8px";
    legend.style.boxShadow = "0 2px 8px rgba(0,0,0,0.15)";
    legend.style.fontFamily = "Arial, sans-serif";
    legend.style.fontSize = "14px";
    legend.style.zIndex = "999";

    const title = document.createElement("div");
    title.textContent = "Module Status";
    title.style.fontWeight = "bold";
    title.style.marginBottom = "10px";
    legend.appendChild(title);

    for (const item of [
      { label: "Producing", color: "#f39c12" },
      { label: "Transporting", color: "#3498db" },
      { label: "Installing", color: "#27ae60" },
      { label: "Not in schedule / upcoming", color: "#95a5a6" },
      { label: "Selected", color: "#e74c3c" },
    ]) {
      const row = document.createElement("div");
      row.style.display = "flex";
      row.style.alignItems = "center";
      row.style.marginBottom = "6px";
      const swatch = document.createElement("span");
      swatch.style.display = "inline-block";
      swatch.style.width = "16px";
      swatch.style.height = "16px";
      swatch.style.background = item.color;
      swatch.style.marginRight = "8px";
      swatch.style.border = "1px solid #666";
      const text = document.createElement("span");
      text.textContent = item.label;
      row.appendChild(swatch);
      row.appendChild(text);
      legend.appendChild(row);
    }
    container.style.position = "relative";
    container.appendChild(legend);
  }

  createLegend();

  function showSelectedModuleInfo(moduleId, colorStatus) {
    let infoBox = document.getElementById("module-info-box");
    if (!infoBox) {
      infoBox = document.createElement("div");
      infoBox.id = "module-info-box";
      infoBox.style.position = "absolute";
      infoBox.style.left = "20px";
      infoBox.style.bottom = "20px";
      infoBox.style.padding = "10px 14px";
      infoBox.style.background = "rgba(0,0,0,0.75)";
      infoBox.style.color = "#fff";
      infoBox.style.borderRadius = "8px";
      infoBox.style.fontFamily = "Arial, sans-serif";
      infoBox.style.fontSize = "14px";
      infoBox.style.zIndex = "999";
      container.appendChild(infoBox);
    }
    infoBox.innerHTML = `
      <div><strong>Module:</strong> ${moduleId}</div>
      <div><strong>Status:</strong> ${colorStatus}</div>
    `;
  }

  container.addEventListener("dblclick", async () => {
    try {
      const result = await caster.castRay();
      if (!result?.fragments) return;
      const key = `${result.fragments.modelId}:${result.localId}`;
      const moduleId = localIdToModule[key];
      if (!moduleId) return;
      const moduleModelIdMap = moduleToModelIdMap[moduleId];
      if (!moduleModelIdMap) return;
      const colorStatus = moduleToStatus[moduleId] || "unknown";
      showSelectedModuleInfo(moduleId, colorStatus);
      await highlighter.highlightByID("select", moduleModelIdMap, true);
    } catch (error) {
      console.warn("Pick failed:", error);
    }
  });
} catch (error) {
  console.warn("Status coloring skipped:", error);
}

setStatus("");

window.addEventListener("beforeunload", () => {
  components.dispose();
  URL.revokeObjectURL(workerUrl);
});
