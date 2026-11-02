// The typed API client. Types come from docs/openapi.json (`npm run gen:api`;
// CI fails when they are stale), so a change to the API that the site does
// not follow is a type error, not a blank page.
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

export const api = createClient<paths>({ baseUrl: "/" });

// Errors are RFC 9457 problem details. Their title and detail are shown as
// the API words them.
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Partial<Schemas["Problem"]>,
  ) {
    super(problem.detail ?? problem.title ?? `HTTP ${status}`);
  }
}

// Unwraps an openapi-fetch result for TanStack Query: the data, or a thrown ApiError.
export async function unwrap<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await request;
  if (error !== undefined || data === undefined) {
    throw new ApiError(response.status, (error ?? {}) as Partial<Schemas["Problem"]>);
  }
  return data;
}
