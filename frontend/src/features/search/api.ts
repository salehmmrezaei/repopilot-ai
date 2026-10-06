import { z } from 'zod';
import { requestJSON, writeHeaders } from '../../lib/api/http';
const job = z.object({
  id: z.string(),
  status: z.string(),
  stage: z.string(),
  mode: z.string(),
  documents_stored: z.number(),
  reserved_tokens: z.number(),
  input_tokens: z.number(),
  token_budget: z.number(),
  price_per_million: z.number(),
  error_message: z.string().nullable(),
});
const state = z.object({
  enabled_semantic: z.boolean(),
  latest: job.nullable(),
  keyword: job.nullable(),
  hybrid: job.nullable(),
});
export type SearchState = z.infer<typeof state>;
export type SearchMode = 'keyword' | 'hybrid';
const response = z.object({
  commit_sha: z.string(),
  mode: z.string(),
  pipeline_version: z.string(),
  provider_profile: z.string(),
  duration_ms: z.number(),
  context_tokens: z.number(),
  context_omitted: z.number(),
  query_tokens: z.number(),
  estimated_query_cost_usd: z.number(),
  results: z.array(
    z.object({
      chunk_id: z.string(),
      path: z.string(),
      symbol: z.string().nullable(),
      start_line: z.number(),
      end_line: z.number(),
      content: z.string(),
      score: z.number(),
      channel_ranks: z.record(z.string(), z.number()),
    }),
  ),
  context: z.array(z.object({ citation_id: z.string(), chunk_id: z.string() })),
});
export type SearchResult = z.infer<typeof response>;
export async function getSearchState(
  id: string,
  signal: AbortSignal,
): Promise<SearchState> {
  return state.parse(
    await requestJSON(`/repositories/${id}/search`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(12000)]),
    }),
  );
}
export async function prepareSearch(
  id: string,
  mode: SearchMode,
  csrf: string,
): Promise<void> {
  job.parse(
    await requestJSON(`/repositories/${id}/search/prepare`, {
      method: 'POST',
      headers: writeHeaders(csrf),
      body: JSON.stringify({ mode }),
    }),
  );
}
export async function cancelSearch(id: string, csrf: string): Promise<void> {
  await requestJSON(`/repositories/${id}/search/cancel`, {
    method: 'POST',
    headers: writeHeaders(csrf),
    body: '{}',
  });
}
export async function querySearch(
  id: string,
  query: string,
  mode: SearchMode,
  csrf: string,
  signal: AbortSignal,
  rerank = false,
): Promise<SearchResult> {
  return response.parse(
    await requestJSON(`/repositories/${id}/search/query`, {
      method: 'POST',
      headers: writeHeaders(csrf),
      body: JSON.stringify({
        query,
        mode,
        rerank,
        top_k: 8,
        context_token_budget: 6000,
      }),
      signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
    }),
  );
}
