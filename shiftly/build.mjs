import { readFile, writeFile, mkdir, cp } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, join } from "node:path";
import { createHash } from "node:crypto";

export const root = fileURLToPath(new URL(".", import.meta.url));
const escape = value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
export async function build(output = join(root, "dist"), contentPath = join(root, "content/returns.txt")) {
  const bytes = await readFile(contentPath);
  const policy = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  if (!policy.trim() || bytes.length > 80000 || policy.includes("\u0000")) throw new Error("Invalid returns policy");
  await mkdir(output, { recursive: true });
  await mkdir(join(output, "returns"), { recursive: true });
  await mkdir(join(output, "content"), { recursive: true });
  await cp(join(root, "public"), output, { recursive: true });
  for (const [template, target] of [["index.html", "index.html"], ["returns.html", "returns/index.html"]]) {
    const html = (await readFile(join(root, template), "utf8")).replace("{{RETURNS_POLICY}}", escape(policy));
    await writeFile(join(output, target), html);
  }
  await writeFile(join(output, "content/returns.txt"), bytes);
  await writeFile(join(output, "build.json"), JSON.stringify({
    commit: process.env.VERCEL_GIT_COMMIT_SHA || process.env.SHIFTLY_COMMIT || null,
    policy_sha256: createHash("sha256").update(bytes).digest("hex")
  }));
  return output;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log(`Shiftly built: ${await build()}`);
}
