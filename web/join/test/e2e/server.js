import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname } from "node:path";
const root = fileURLToPath(new URL("../../", import.meta.url));
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json" };
createServer(async (request, response) => {
  try {
    const path = resolve(root, `.${new URL(request.url, "http://localhost").pathname === "/" ? "/index.html" : new URL(request.url, "http://localhost").pathname}`);
    if (!path.startsWith(root) || path.includes("node_modules")) throw new Error("not found");
    response.writeHead(200, { "content-type": types[extname(path)] || "text/plain", "cache-control": "no-store" });
    response.end(await readFile(path));
  } catch { response.writeHead(404); response.end(); }
}).listen(4174, "127.0.0.1");
