import { z } from 'zod';
import { requestJSON, writeHeaders } from '../../lib/api/http';
const evidence = z.object({
  citation_id: z.string(),
  chunk_id: z.string(),
  path: z.string(),
  commit_sha: z.string(),
  start_line: z.number(),
  end_line: z.number(),
  content: z.string(),
});
const runSchema = z.object({
  id: z.string(),
  conversation_id: z.string(),
  request_key: z.string(),
  question: z.string(),
  mode: z.enum(['investigate', 'propose']).default('investigate'),
  source_index_id: z.string(),
  commit_sha: z.string(),
  model: z.string(),
  status: z.enum(['queued', 'running', 'completed', 'failed', 'cancelled']),
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  created_at: z.string(),
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
  result: z
    .object({
      answer: z.object({
        status: z.enum(['answered', 'insufficient_evidence']),
        claims: z.array(
          z.object({ text: z.string(), citation_ids: z.array(z.string()) }),
        ),
        limitation: z.string(),
      }),
      evidence: z.array(evidence),
      proposal: z
        .object({
          implementation_plan: z.array(z.string()).max(6),
          risks: z.array(z.string()).max(5),
          test_plan: z.array(z.string()).max(6),
          files: z
            .array(
              z.object({
                path: z.string(),
                operation: z.enum(['replace', 'create', 'delete']),
                before_sha256: z.string().nullable(),
                after_sha256: z.string().nullable(),
              }),
            )
            .max(4),
          diff: z.string().max(65536),
          diff_sha256: z.string(),
          validation: z.literal('source_checked_tests_not_run'),
        })
        .nullable()
        .default(null),
    })
    .nullable(),
});
export const eventSchema = z.object({
  sequence: z.number().int().min(1).max(32),
  kind: z.string(),
  summary: z.string().max(2000),
  tool: z.string().nullable(),
  duration_ms: z.number().nullable(),
  created_at: z.string(),
});
const detailSchema = z.object({
  run: runSchema,
  events: z.array(eventSchema).max(32),
  calls: z
    .array(
      z.object({
        step: z.number(),
        input_rate: z.string(),
        output_rate: z.string(),
        input_tokens: z.number().nullable(),
        output_tokens: z.number().nullable(),
        cost_usd: z.string().nullable(),
        created_at: z.string(),
        finished_at: z.string().nullable(),
      }),
    )
    .max(5),
});
export type AgentRun = z.infer<typeof runSchema>;
export type AgentDetail = z.infer<typeof detailSchema>;
export type AgentEvent = z.infer<typeof eventSchema>;
export interface Submission {
  request_key: string;
  question: string;
  mode: 'investigate' | 'propose';
}
export async function listAgents(id: string, signal: AbortSignal) {
  return z.object({ enabled: z.boolean(), items: z.array(runSchema) }).parse(
    await requestJSON(`/conversations/${id}/investigations`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(12000)]),
    }),
  );
}
export async function getAgent(
  id: string,
  signal: AbortSignal,
): Promise<AgentDetail> {
  return detailSchema.parse(
    await requestJSON(`/agent-runs/${id}`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(12000)]),
    }),
  );
}
export async function submitAgent(
  id: string,
  data: Submission,
  csrf: string,
): Promise<AgentRun> {
  return runSchema.parse(
    await requestJSON(`/conversations/${id}/investigations`, {
      method: 'POST',
      headers: writeHeaders(csrf),
      body: JSON.stringify(data),
    }),
  );
}
export async function cancelAgent(id: string, csrf: string): Promise<AgentRun> {
  return runSchema.parse(
    await requestJSON(`/agent-runs/${id}/cancel`, {
      method: 'POST',
      headers: writeHeaders(csrf),
      body: '{}',
    }),
  );
}
