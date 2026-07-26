import {
  createCipheriv,
  createDecipheriv,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";

const VERSION = 1;
const ALGORITHM = "A256GCM";
const TAG_BYTES = 16;

function keyFromEnvironment() {
  const raw = process.env.PJ23_FEED_KEY || "";
  const key = Buffer.from(raw, "base64");
  if (key.length !== 32 || key.toString("base64").replace(/=+$/, "") !== raw.replace(/=+$/, "")) {
    throw new Error("PJ23_FEED_KEY must be one canonical base64-encoded 256-bit key");
  }
  return key;
}

function aadFor(envelope) {
  return Buffer.from(
    JSON.stringify({
      v: envelope.v,
      alg: envelope.alg,
      kind: envelope.kind,
      created_at: envelope.created_at,
    }),
    "utf8",
  );
}

function seal(plaintext, kind, key, createdAt = new Date().toISOString()) {
  const envelope = {
    v: VERSION,
    alg: ALGORITHM,
    kind,
    created_at: createdAt,
  };
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, iv, { authTagLength: TAG_BYTES });
  cipher.setAAD(aadFor(envelope));
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const payload = Buffer.concat([ciphertext, cipher.getAuthTag()]);
  return {
    ...envelope,
    iv: iv.toString("base64"),
    ciphertext: payload.toString("base64"),
  };
}

function open(envelope, expectedKind, key) {
  if (
    envelope?.v !== VERSION ||
    envelope?.alg !== ALGORITHM ||
    envelope?.kind !== expectedKind ||
    typeof envelope.created_at !== "string"
  ) {
    throw new Error("invalid encrypted envelope metadata");
  }
  const iv = Buffer.from(envelope.iv || "", "base64");
  const payload = Buffer.from(envelope.ciphertext || "", "base64");
  if (iv.length !== 12 || payload.length <= TAG_BYTES) {
    throw new Error("invalid encrypted envelope bytes");
  }
  const body = payload.subarray(0, -TAG_BYTES);
  const tag = payload.subarray(-TAG_BYTES);
  const decipher = createDecipheriv("aes-256-gcm", key, iv, { authTagLength: TAG_BYTES });
  decipher.setAAD(aadFor(envelope));
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(body), decipher.final()]);
}

async function encryptFile(inputPath, outputPath, kind) {
  const key = keyFromEnvironment();
  const plaintext = await readFile(inputPath);
  const envelope = seal(plaintext, kind, key);
  try {
    const previous = JSON.parse(await readFile(outputPath, "utf8"));
    const previousIv = Buffer.from(previous.iv || "", "base64");
    const nextIv = Buffer.from(envelope.iv, "base64");
    if (previousIv.length === nextIv.length && timingSafeEqual(previousIv, nextIv)) {
      throw new Error("fatal: AES-GCM nonce reuse detected");
    }
  } catch (error) {
    if (error?.message === "fatal: AES-GCM nonce reuse detected") throw error;
  }
  await writeFile(outputPath, `${JSON.stringify(envelope)}\n`, "utf8");
  process.stdout.write(`encrypted ${kind}: ${plaintext.length} bytes\n`);
}

async function decryptFile(inputPath, outputPath, kind) {
  const key = keyFromEnvironment();
  const envelope = JSON.parse(await readFile(inputPath, "utf8"));
  const plaintext = open(envelope, kind, key);
  await writeFile(outputPath, plaintext);
  process.stdout.write(`decrypted ${kind}: ${plaintext.length} bytes\n`);
}

function selfTest() {
  const key = randomBytes(32);
  const sample = Buffer.from("pj23 crypto self-test", "utf8");
  const first = seal(sample, "self-test", key, "2026-01-01T00:00:00.000Z");
  const second = seal(sample, "self-test", key, "2026-01-01T00:00:00.000Z");
  if (first.iv === second.iv) throw new Error("self-test nonce collision");
  if (!timingSafeEqual(open(first, "self-test", key), sample)) {
    throw new Error("self-test round trip failed");
  }
  const tampered = { ...first, ciphertext: `A${first.ciphertext.slice(1)}` };
  let rejected = false;
  try {
    open(tampered, "self-test", key);
  } catch {
    rejected = true;
  }
  if (!rejected) throw new Error("self-test tamper was not rejected");
  process.stdout.write("AES-256-GCM self-test passed\n");
}

const [command, inputPath, outputPath, kind] = process.argv.slice(2);
if (command === "encrypt" && inputPath && outputPath && kind) {
  await encryptFile(inputPath, outputPath, kind);
} else if (command === "decrypt" && inputPath && outputPath && kind) {
  await decryptFile(inputPath, outputPath, kind);
} else if (command === "self-test") {
  selfTest();
} else {
  throw new Error(
    "usage: node pj23_crypto.mjs encrypt|decrypt <input> <output> <kind> | self-test",
  );
}
