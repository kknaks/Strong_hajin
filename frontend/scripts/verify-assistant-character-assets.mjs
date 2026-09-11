import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifestPath = path.join(frontendRoot, "assets/assistant-character/provenance.json");
const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
const performanceEvidence = JSON.parse(await readFile(
  path.join(frontendRoot, "assets/assistant-character/performance-evidence.json"),
  "utf8",
));
const expectedKeys = [
  "cream-cat",
  "silver-tabby",
  "tuxedo-cat",
  "calico-cat",
  "puppy",
  "rabbit",
  "bear",
  "chick",
  "red-panda",
];

if (manifest.schema_version !== 1) throw new Error("unsupported assistant character provenance schema");
if (JSON.stringify(manifest.assets.map((asset) => asset.key)) !== JSON.stringify(expectedKeys)) {
  throw new Error("provenance catalog keys or ordering do not match the product catalog");
}
if (!manifest.generation.tool || !manifest.generation.model || !manifest.generation.session_id) {
  throw new Error("generation tool, model disclosure, and session evidence are required");
}
if (!manifest.terms_snapshot.url || !manifest.similarity_review.result) {
  throw new Error("terms and similarity review records are required");
}
if (performanceEvidence.measurement.catalog_assets !== manifest.assets.length) {
  throw new Error("performance evidence does not cover the complete catalog");
}
for (const [measurement, maximum] of [
  ["selected_asset_bytes_worst_case", "selected_asset_bytes_max"],
  ["catalog_transfer_bytes", "catalog_transfer_bytes_max"],
  ["catalog_decoded_rgba_bytes", "catalog_decoded_rgba_bytes_max"],
  ["selected_cold_load_and_decode_ms", "selected_cold_load_and_decode_ms_max"],
  ["catalog_cold_load_and_decode_ms", "catalog_cold_load_and_decode_ms_max"],
  ["frame_p95_ms", "frame_p95_ms_max"],
  ["picker_js_heap_delta_bytes", "picker_js_heap_delta_bytes_max"],
  ["idle_main_thread_task_ms_per_2s", "idle_main_thread_task_ms_per_2s_max"],
]) {
  if (performanceEvidence.measurement[measurement] > performanceEvidence.budgets[maximum]) {
    throw new Error(`${measurement} exceeds recorded budget`);
  }
}

let totalExportBytes = 0;
for (const asset of manifest.assets) {
  if (!manifest.prompts[asset.prompt_id]) throw new Error(`${asset.key}: missing prompt`);
  if (!manifest.reference_inputs[asset.reference_input]) throw new Error(`${asset.key}: missing reference input`);
  for (const variant of ["source", "export"]) {
    const record = asset[variant];
    const absolutePath = path.join(frontendRoot, record.file);
    const bytes = await readFile(absolutePath);
    const actualHash = createHash("sha256").update(bytes).digest("hex");
    if (actualHash !== record.sha256) throw new Error(`${asset.key}: ${variant} checksum mismatch`);
    if ((await stat(absolutePath)).size !== (variant === "export" ? record.bytes : bytes.length)) {
      throw new Error(`${asset.key}: ${variant} byte count mismatch`);
    }
  }
  if (Math.max(asset.export.width, asset.export.height) > manifest.production_export.max_dimension_px) {
    throw new Error(`${asset.key}: export dimensions exceed budget`);
  }
  if (asset.export.bytes > manifest.production_export.individual_transfer_budget_bytes) {
    throw new Error(`${asset.key}: export transfer size exceeds budget`);
  }
  totalExportBytes += asset.export.bytes;
}

if (totalExportBytes > manifest.production_export.catalog_transfer_budget_bytes) {
  throw new Error(`catalog transfer size exceeds budget: ${totalExportBytes}`);
}

console.log(JSON.stringify({
  result: "assistant character source and production exports verified",
  assets: manifest.assets.length,
  total_export_bytes: totalExportBytes,
  largest_export_bytes: Math.max(...manifest.assets.map((asset) => asset.export.bytes)),
}));
