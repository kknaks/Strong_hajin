#!/usr/bin/env node
/**
 * 탐침 셸의 **자리만 채우는** 아이콘을 만든다.
 *
 * 왜 필요한가 — `tauri.conf.json` 의 `bundle.icon` 이 가리키는 파일이 없으면 Windows 쪽
 * 리소스 생성과 번들 단계가 멈춘다. 그런데 **Strong Hajin 의 실제 아이콘은 아직 없다**(Phase 3·7).
 * 그래서 「없는 것을 그린 척」하지 않고 **단색 사각형**을 프로그램으로 만든다.
 *
 * 이 파일들은 제품 아이콘이 아니다. Phase 7 이 진짜 아이콘 세트로 갈아끼운다.
 * 실행: `node scripts/gen-placeholder-icons.mjs` (frontend/src-tauri 에서)
 */
import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const iconsDir = join(here, "..", "icons");

/** 제품 화면의 강조색과 같은 계열 — 값은 자리표시일 뿐이다. */
const RGBA = [79, 99, 239, 255];

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

function png(size) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8; // bit depth
  header[9] = 6; // RGBA
  // 각 행 앞에 filter 바이트 0 을 둔다 — 가장 단순한 형태.
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(size * 4)]);
  for (let x = 0; x < size; x += 1) row.set(RGBA, 1 + x * 4);
  const raw = Buffer.concat(Array.from({ length: size }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** ICO 는 PNG 를 그대로 품을 수 있다(Vista 이후). 32x32 하나만 넣는다. */
function ico(pngBuffer, size) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(1, 4); // count
  const entry = Buffer.alloc(16);
  entry[0] = size === 256 ? 0 : size;
  entry[1] = size === 256 ? 0 : size;
  entry.writeUInt16LE(1, 4); // color planes
  entry.writeUInt16LE(32, 6); // bits per pixel
  entry.writeUInt32LE(pngBuffer.length, 8);
  entry.writeUInt32LE(header.length + entry.length, 12);
  return Buffer.concat([header, entry, pngBuffer]);
}

mkdirSync(iconsDir, { recursive: true });
const written = [];
for (const [name, size] of [
  ["32x32.png", 32],
  ["128x128.png", 128],
  ["128x128@2x.png", 256],
  ["icon.png", 512],
]) {
  writeFileSync(join(iconsDir, name), png(size));
  written.push(name);
}
writeFileSync(join(iconsDir, "icon.ico"), ico(png(32), 32));
written.push("icon.ico");
console.log(`자리표시 아이콘 생성: ${written.join(" · ")}`);
console.log("⚠ 제품 아이콘이 아니다 — Phase 7 이 실제 세트로 갈아끼운다.");
