import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build, root } from "./build.mjs";

test("the deployed raw content and visible policy preserve the reviewed bytes", async () => {
  const dir = await mkdtemp(join(tmpdir(), "shiftly-"));
  try {
    const input = join(dir, "input.txt");
    const content = "Items purchased on or after 2026-11-04 can be returned within 30 days.\nA & B <safe>";
    await writeFile(input, content);
    process.env.SHIFTLY_COMMIT = "a".repeat(40);
    await build(join(dir, "out"), input);
    assert.equal(await readFile(join(dir, "out/content/returns.txt"), "utf8"), content);
    const html = await readFile(join(dir, "out/returns/index.html"), "utf8");
    assert.ok(html.includes('<p id="returns-policy">' + content.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;") + '</p>'));
    assert.equal(JSON.parse(await readFile(join(dir, "out/build.json"))).commit, "a".repeat(40));
    assert.equal((html.match(/id="returns-policy"/g) || []).length, 1);
    assert.ok((await readFile(join(dir, "out/index.html"), "utf8")).includes('/returns/'));
  } finally { delete process.env.SHIFTLY_COMMIT; await rm(dir, { recursive: true, force: true }); }
});

test("the initial storefront is demonstrably outdated and no build changes source content", async () => {
  const source = await readFile(join(root, "content/returns.txt"), "utf8");
  assert.equal(source, "All customers can return items within 60 days.");
  const dir = await mkdtemp(join(tmpdir(), "shiftly-seed-"));
  try {
    await build(dir);
    assert.ok((await readFile(join(dir, "returns/index.html"), "utf8")).includes(source));
    assert.equal(await readFile(join(root, "content/returns.txt"), "utf8"), source);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
