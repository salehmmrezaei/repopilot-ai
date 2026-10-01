import { z } from 'zod';
const command = z.object({
  status: z.enum([
    'passed',
    'failed',
    'timeout',
    'output_limit',
    'resource_limit',
    'error',
  ]),
  exit_code: z.number().nullable(),
  log: z.string().max(32768),
  truncated: z.boolean(),
  duration_ms: z.number(),
});
export const runSchema = z.object({
  id: z.string(),
  request_key: z.string(),
  profile: z.string(),
  status: z.enum(['queued', 'running', 'completed', 'failed', 'cancelled']),
  stage: z.string(),
  error: z.string().nullable(),
  patch_sha256: z.string(),
  result: z
    .object({
      image_id: z.string(),
      archive_sha256: z.string().nullable(),
      profile: z.string().nullable(),
      command: z.array(z.string()),
      preparation: command.nullable(),
      baseline: command.nullable(),
      patched: command.nullable(),
    })
    .nullable(),
});
