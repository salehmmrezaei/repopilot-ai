import { z } from 'zod';
import { requestJSON, writeHeaders } from '../../lib/api/http';
export const repositorySchema = z.object({
  id: z.string(),
  owner: z.string(),
  name: z.string(),
  url: z.string(),
  github_repository_id: z.number().nullable(),
  default_branch: z.string().nullable(),
  last_commit_sha: z.string().nullable(),
  imported_at: z.string().nullable(),
  job: z.object({
    id: z.string(),
    status: z.enum(['queued', 'running', 'completed', 'failed', 'cancelled']),
    stage: z.string(),
    attempts: z.number(),
    files_scanned: z.number(),
    files_stored: z.number(),
    files_skipped: z.number(),
    error_code: z.string().nullable(),
    error_message: z.string().nullable(),
  }),
});
export type Repository = z.infer<typeof repositorySchema>;
const fileSchema = z.object({
  id: z.string(),
  path: z.string(),
  language: z.string(),
  size: z.number(),
});
const fileContentSchema = fileSchema.extend({
  content: z.string(),
  commit_sha: z.string(),
});
const pageSchema = z.object({
  items: z.array(fileSchema),
  next_offset: z.number().nullable(),
});
export type SourceFile = z.infer<typeof fileSchema>;
export type FileContent = z.infer<typeof fileContentSchema>;
export async function listRepositories(
  signal: AbortSignal,
): Promise<Repository[]> {
  return z
    .array(repositorySchema)
    .parse(await requestJSON('/repositories', { signal }));
}
export async function createRepository(
  url: string,
  csrf: string,
): Promise<void> {
  repositorySchema.parse(
    await requestJSON('/repositories', {
      method: 'POST',
      headers: writeHeaders(csrf),
      body: JSON.stringify({ url }),
    }),
  );
}
export async function repositoryAction(
  id: string,
  action: 'retry' | 'refresh' | 'cancel' | 'delete',
  csrf: string,
): Promise<void> {
  await requestJSON(
    `/repositories/${id}${action === 'delete' ? '' : '/' + action}`,
    {
      method: action === 'delete' ? 'DELETE' : 'POST',
      headers: writeHeaders(csrf),
      body: '{}',
    },
  );
}
export async function listFiles(
  id: string,
  offset: number,
  signal: AbortSignal,
) {
  return pageSchema.parse(
    await requestJSON(`/repositories/${id}/files?offset=${offset}&limit=100`, {
      signal,
    }),
  );
}
export async function readFile(
  id: string,
  fileId: string,
  signal: AbortSignal,
): Promise<FileContent> {
  return fileContentSchema.parse(
    await requestJSON(`/repositories/${id}/files/${fileId}`, { signal }),
  );
}
