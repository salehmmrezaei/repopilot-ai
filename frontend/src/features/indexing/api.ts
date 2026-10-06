import { z } from 'zod';
import { requestJSON, writeHeaders } from '../../lib/api/http';
const indexSchema = z.object({
  id: z.string(),
  status: z.enum(['queued', 'running', 'completed', 'failed', 'cancelled']),
  stage: z.string(),
  commit_sha: z.string(),
  pipeline_version: z.string(),
  files_stored: z.number(),
  symbol_count: z.number(),
  chunk_count: z.number(),
  error_message: z.string().nullable(),
  diagnostics: z.array(
    z.object({ file_id: z.string(), path: z.string(), message: z.string() }),
  ),
});
const stateSchema = z.object({
  latest: indexSchema.nullable(),
  active: indexSchema.nullable(),
  current_pipeline_version: z.string().optional(),
  rebuild_available: z.boolean().default(false),
});
export type IndexState = z.infer<typeof stateSchema>;
const fileSchema = z.object({
  index_id: z.string(),
  file_id: z.string(),
  path: z.string(),
  language: z.string(),
  commit_sha: z.string(),
  symbols: z.array(
    z.object({
      id: z.string(),
      ordinal: z.number(),
      qualified_name: z.string(),
      kind: z.string(),
      signature: z.string().nullable().optional(),
      docstring: z.string().nullable().optional(),
      start_line: z.number(),
      end_line: z.number(),
    }),
  ),
  chunks: z.array(
    z.object({
      id: z.string(),
      ordinal: z.number(),
      kind: z.string(),
      start_line: z.number(),
      end_line: z.number(),
      start_offset: z.number(),
      end_offset: z.number(),
      content: z.string(),
    }),
  ),
  next_symbol_offset: z.number().nullable(),
  next_chunk_offset: z.number().nullable(),
});
export type IndexedFile = z.infer<typeof fileSchema>;
export async function getIndex(
  id: string,
  signal: AbortSignal,
): Promise<IndexState> {
  return stateSchema.parse(
    await requestJSON(`/repositories/${id}/index`, { signal }),
  );
}
export async function indexAction(
  id: string,
  action: 'start' | 'cancel',
  csrf: string,
): Promise<void> {
  const result = await requestJSON(
    `/repositories/${id}/index${action === 'cancel' ? '/cancel' : ''}`,
    {
      method: 'POST',
      headers: writeHeaders(csrf),
      body: '{}',
    },
  );
  if (action === 'start') indexSchema.parse(result);
}
export async function getIndexedFile(
  id: string,
  fileId: string,
  symbols: number,
  chunks: number,
  signal: AbortSignal,
): Promise<IndexedFile> {
  return fileSchema.parse(
    await requestJSON(
      `/repositories/${id}/index/files/${fileId}?symbol_offset=${symbols}&chunk_offset=${chunks}`,
      { signal },
    ),
  );
}
