import "@testing-library/jest-dom";
import http from "http";
import https from "https";
import { URL } from "url";

// Ensure Element.prototype.scrollIntoView is defined in JSDOM so citation chip scrolling can be spied on
if (typeof window !== "undefined" && typeof window.HTMLElement !== "undefined") {
  if (!window.HTMLElement.prototype.scrollIntoView) {
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
  }
}

// Provide a lightweight Node http/https fetch implementation if jest-environment-jsdom strips global fetch
if (typeof globalThis.fetch === "undefined") {
  globalThis.fetch = ((
    input: RequestInfo | URL,
    init?: RequestInit
  ): Promise<Response> => {
    return new Promise((resolve, reject) => {
      const urlStr = typeof input === "string" ? input : input.toString();
      const parsed = new URL(urlStr);
      const client = parsed.protocol === "https:" ? https : http;
      const method = init?.method || "GET";
      const headers: Record<string, string> = {};
      if (init?.headers) {
        if (Array.isArray(init.headers)) {
          for (const [k, v] of init.headers) headers[k] = v;
        } else if (typeof (init.headers as Headers).forEach === "function") {
          (init.headers as Headers).forEach((v, k) => {
            headers[k] = v;
          });
        } else {
          Object.assign(headers, init.headers as Record<string, string>);
        }
      }

      const bodyStr =
        typeof init?.body === "string" ? init.body : undefined;
      if (bodyStr !== undefined && !headers["Content-Length"]) {
        headers["Content-Length"] = String(Buffer.byteLength(bodyStr, "utf8"));
      }

      const req = client.request(
        parsed,
        {
          method,
          headers,
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (c: Buffer) => chunks.push(c));
          res.on("end", () => {
            const rawText = Buffer.concat(chunks).toString("utf8");
            const status = res.statusCode || 200;
            const statusText = res.statusMessage || "OK";
            const responseLike = {
              ok: status >= 200 && status < 300,
              status,
              statusText,
              text: async () => rawText,
              json: async () => JSON.parse(rawText),
            } as unknown as Response;
            resolve(responseLike);
          });
        }
      );

      req.on("error", (err) => reject(err));
      if (bodyStr !== undefined) {
        req.write(bodyStr);
      }
      req.end();
    });
  }) as typeof fetch;
}

