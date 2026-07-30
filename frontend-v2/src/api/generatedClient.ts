import createClient from "openapi-fetch";

import type { paths } from "./schema.generated";


export class ApiContractError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly payload: unknown,
  ) {
    super(message);
    this.name = "ApiContractError";
  }
}


export const generatedClient = createClient<paths>({
  baseUrl: "",
  credentials: "same-origin",
  headers: { "Content-Type": "application/json" },
});


generatedClient.use({
  async onResponse({ response }) {
    if (response.ok) return response;
    let payload: unknown;
    try {
      payload = await response.clone().json();
    } catch {
      payload = await response.clone().text();
    }
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : `Request failed: ${response.status}`;
    throw new ApiContractError(detail, response.status, payload);
  },
});
