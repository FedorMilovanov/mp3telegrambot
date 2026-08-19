from __future__ import annotations

from pathlib import Path

path = Path("provider/server/src/session_manager.ts")
source = path.read_text(encoding="utf-8")

axios_import = 'import axios, { AxiosRequestConfig } from "axios";\n'
if source.count(axios_import) != 1:
    raise SystemExit("FORWARD247_AXIOS_IMPORT_ANCHOR_DRIFT")
source = source.replace(axios_import, "", 1)

https_import = 'import { Agent } from "node:https";'
https_replacement = 'import { Agent, request as httpsRequest } from "node:https";'
if source.count(https_import) != 1:
    raise SystemExit("FORWARD247_HTTPS_IMPORT_ANCHOR_DRIFT")
source = source.replace(https_import, https_replacement, 1)

method_anchor = "    private getFetch(\n"
if source.count(method_anchor) != 1:
    raise SystemExit("FORWARD247_METHOD_ANCHOR_DRIFT")

raw_method = r'''    private async rawHttpsRequest(
        url: string | URL,
        options: any,
        agent: Agent | undefined,
    ): Promise<{ status: number; json: unknown; body: string }> {
        const method = (options?.method || "GET").toUpperCase();
        const headers = { ...(options?.headers || {}) };
        let body: string | undefined;
        if (options?.body !== undefined && options?.body !== null) {
            body =
                typeof options.body === "string"
                    ? options.body
                    : JSON.stringify(options.body);
            headers["Content-Length"] = Buffer.byteLength(body);
        }

        return await new Promise((resolve, reject) => {
            const req = httpsRequest(
                url,
                { method, headers, agent },
                (res) => {
                    const chunks: Buffer[] = [];
                    res.on("data", (chunk: Buffer) => chunks.push(chunk));
                    res.on("end", () => {
                        const responseBody = Buffer.concat(chunks).toString("utf8");
                        let parsed: unknown;
                        try {
                            parsed = JSON.parse(responseBody);
                        } catch {
                            parsed = responseBody;
                        }
                        const status = res.statusCode || 0;
                        if (status < 200 || status >= 300) {
                            reject(new Error(`HTTP ${status} for ${url}`));
                            return;
                        }
                        resolve({
                            status,
                            json: parsed,
                            body: responseBody,
                        });
                    });
                },
            );
            req.on("error", reject);
            req.setTimeout(30000, () =>
                req.destroy(new Error("request timeout")),
            );
            if (body) req.write(body);
            req.end();
        });
    }

'''
source = source.replace(method_anchor, raw_method + method_anchor, 1)

old_block = r'''                    const axiosOpt: AxiosRequestConfig = {
                        headers: options?.headers,
                        params: options?.params,
                        httpsAgent: proxySpec.asDispatcher(logger),
                    };
                    const response = await (method === "GET"
                        ? axios.get(url, axiosOpt)
                        : axios.post(url, options?.body, axiosOpt));

                    return {
                        ok: response.status >= 200 && response.status < 300,
                        status: response.status,
                        json: async () => response.data,
                        text: async () =>
                            typeof response.data === "string"
                                ? response.data
                                : JSON.stringify(response.data),
                    };'''
new_block = r'''                    const response = await this.rawHttpsRequest(
                        url,
                        options,
                        proxySpec.asDispatcher(logger),
                    );

                    return {
                        ok: response.status >= 200 && response.status < 300,
                        status: response.status,
                        json: async () => response.json,
                        text: async () => response.body,
                    };'''
if source.count(old_block) != 1:
    raise SystemExit("FORWARD247_GETFETCH_ANCHOR_DRIFT")
source = source.replace(old_block, new_block, 1)

retry_anchor = "        const bgFetch = this.getFetch(pxySpec, 3, 5000);"
if source.count(retry_anchor) != 1:
    raise SystemExit("FORWARD247_RETRY_ANCHOR_DRIFT")
source = source.replace(
    retry_anchor,
    "        const bgFetch = this.getFetch(pxySpec, 5, 8000);",
    1,
)

path.write_text(source, encoding="utf-8")
print("FORWARD247_APPLIED=1")
