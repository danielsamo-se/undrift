import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { resolve, sep, extname } from "node:path";
import { root } from "./build.mjs";
const dist = resolve(root, "dist");
const types = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8", ".json": "application/json" };
createServer(async (req, res) => {
  try {
    let path = resolve(dist, "." + decodeURIComponent(new URL(req.url, "http://localhost").pathname));
    if (!path.startsWith(dist + sep) && path !== dist) { res.writeHead(403); res.end(); return; }
    if ((await stat(path)).isDirectory()) path = resolve(path, "index.html");
    res.writeHead(200, { "Content-Type": types[extname(path)] || "application/octet-stream", "Cache-Control": "no-store" });
    res.end(await readFile(path));
  } catch { res.writeHead(404); res.end("Page not found"); }
}).listen(Number(process.env.PORT || 8200), "127.0.0.1", () => console.log("Shiftly preview: http://127.0.0.1:" + (process.env.PORT || 8200)));
