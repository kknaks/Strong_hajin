# SCAX assistant character assets

`source/` contains the first retained high-resolution PNG output returned by the image-generation tool. For these AI-generated assets, that output is the source original; there is no separate PSD, Blender scene, model, rig, or texture package.

Runtime AVIF exports live in `src/assets/assistant-character/` so Vite fingerprints only the delivery files. `provenance.json` connects every catalog key to its generation prompt, reference input, session evidence, source checksum, export checksum, terms snapshot, and similarity review. `performance-evidence.json` records the renderer budgets and the latest representative measurement.

Regenerate one runtime export from the frontend directory with:

```sh
sips -s format avif -s formatOptions 90 -Z 640 \
  assets/assistant-character/source/cream-cat.png \
  --out src/assets/assistant-character/cream-cat.avif
npm run verify:assistant-assets
```

When an image changes, update both checksums and the measured byte count in the manifest, then rerun the browser budget journey. Do not claim a model identifier, input license, or source layer that the generation record does not expose.
