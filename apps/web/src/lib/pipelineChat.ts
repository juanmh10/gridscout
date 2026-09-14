import { fetcher } from '../api';

export type PipelineChatActionName =
  | 'overview'
  | 'next_batch'
  | 'explain_criteria'
  | 'opportunity_summary'
  | 'rejection_summary'
  | 'price_conditions'
  | 'market_check'
  | 'ask';

export interface PipelineChatEligibility {
  available: boolean;
  mode?: string;
  reason_code?: string;
  reason_message?: string;
}

export interface PipelineChatThread {
  id?: string;
  next_offset?: number;
  total_candidates?: number;
}

export interface PipelineChatCandidate {
  id?: string;
  listing_id?: string;
  product_id?: string;
  product_name?: string;
  product?: string;
  title?: string;
  summary?: string;
  source_url?: string;
  price?: number | string | null;
  asking_price?: number | string | null;
  estimated_clearing_value?: number | string | null;
  final_score?: number | string | null;
  score?: number | string | null;
  price_edge?: number | string | null;
  margin?: number | string | null;
  condition?: string | null;
  condition_assessment?: string | null;
  reason?: string | null;
  [key: string]: unknown;
}

export interface PipelineChatCitation {
  title?: string;
  url?: string;
  [key: string]: unknown;
}

export interface PipelineChatAction {
  action: PipelineChatActionName | string;
  label?: string;
  requires_product?: boolean;
  requires_text?: boolean;
  [key: string]: unknown;
}

export interface ChatMessage {
  id: string;
  role: string;
  kind: string;
  content: string;
  candidates: PipelineChatCandidate[];
  citations: PipelineChatCitation[];
  actions: PipelineChatAction[];
  created_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface PipelineChatProduct {
  id: string;
  display_name?: string;
  name?: string;
  [key: string]: unknown;
}

export interface PipelineChatResponse {
  eligibility: PipelineChatEligibility;
  thread: PipelineChatThread | null;
  messages: ChatMessage[];
  available_actions: PipelineChatAction[];
  products: PipelineChatProduct[];
}

export interface PipelineChatMessageRequest {
  client_request_id: string;
  action: PipelineChatActionName;
  text?: string;
  product_id?: string;
}

export interface PipelineChatMessageResponse {
  message: ChatMessage;
  thread: PipelineChatThread | null;
  available_actions: PipelineChatAction[];
  products: PipelineChatProduct[];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function normalizeCandidate(value: unknown): PipelineChatCandidate {
  return asRecord(value) as PipelineChatCandidate;
}

function normalizeCitation(value: unknown): PipelineChatCitation {
  return asRecord(value) as PipelineChatCitation;
}

function normalizeAction(value: unknown): PipelineChatAction | null {
  const action = asRecord(value);
  return typeof action.action === 'string' ? action as PipelineChatAction : null;
}

export function normalizeChatMessage(value: unknown): ChatMessage {
  const message = asRecord(value);
  return {
    id: typeof message.id === 'string' ? message.id : `message-${Math.random().toString(36).slice(2)}`,
    role: typeof message.role === 'string' ? message.role : 'assistant',
    kind: typeof message.kind === 'string' ? message.kind : 'chat',
    content: typeof message.content === 'string' ? message.content : '',
    candidates: asArray<unknown>(message.candidates).map(normalizeCandidate),
    citations: asArray<unknown>(message.citations).map(normalizeCitation),
    actions: asArray<unknown>(message.actions).map(normalizeAction).filter((item): item is PipelineChatAction => item !== null),
    created_at: typeof message.created_at === 'string' ? message.created_at : null,
    metadata: asRecord(message.metadata),
  };
}

export function normalizePipelineChat(value: unknown): PipelineChatResponse {
  const payload = asRecord(value);
  const eligibility = asRecord(payload.eligibility);
  const rawThread = payload.thread;
  const threadRecord = rawThread && typeof rawThread === 'object' ? asRecord(rawThread) : null;

  return {
    eligibility: {
      available: eligibility.available === true,
      mode: typeof eligibility.mode === 'string' ? eligibility.mode : undefined,
      reason_code: typeof eligibility.reason_code === 'string' ? eligibility.reason_code : undefined,
      reason_message: typeof eligibility.reason_message === 'string' ? eligibility.reason_message : undefined,
    },
    thread: threadRecord ? {
      id: typeof threadRecord.id === 'string' ? threadRecord.id : undefined,
      next_offset: typeof threadRecord.next_offset === 'number' ? threadRecord.next_offset : 0,
      total_candidates: typeof threadRecord.total_candidates === 'number' ? threadRecord.total_candidates : 0,
    } : null,
    messages: asArray<unknown>(payload.messages).map(normalizeChatMessage),
    available_actions: asArray<unknown>(payload.available_actions).map(normalizeAction).filter((item): item is PipelineChatAction => item !== null),
    products: asArray<unknown>(payload.products).map(value => {
      const product = asRecord(value);
      return {
        id: typeof product.id === 'string' ? product.id : '',
        display_name: typeof product.display_name === 'string' ? product.display_name : undefined,
        name: typeof product.name === 'string' ? product.name : undefined,
        ...product,
      };
    }).filter(product => product.id),
  };
}

export async function getPipelineChat(pipelineId: string): Promise<PipelineChatResponse> {
  const response = await fetcher<unknown>(`/pipelines/${encodeURIComponent(pipelineId)}/chat`);
  return normalizePipelineChat(response);
}

export async function sendPipelineChatMessage(
  pipelineId: string,
  payload: PipelineChatMessageRequest,
): Promise<PipelineChatMessageResponse> {
  const response = await fetcher<unknown>(`/pipelines/${encodeURIComponent(pipelineId)}/chat/messages`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  const data = asRecord(response);
  return {
    message: normalizeChatMessage(data.message),
    thread: data.thread && typeof data.thread === 'object' ? {
      id: typeof asRecord(data.thread).id === 'string' ? asRecord(data.thread).id as string : undefined,
      next_offset: typeof asRecord(data.thread).next_offset === 'number' ? asRecord(data.thread).next_offset as number : 0,
      total_candidates: typeof asRecord(data.thread).total_candidates === 'number' ? asRecord(data.thread).total_candidates as number : 0,
    } : null,
    available_actions: asArray<unknown>(data.available_actions).map(normalizeAction).filter((item): item is PipelineChatAction => item !== null),
    products: asArray<unknown>(data.products).map(value => {
      const product = asRecord(value);
      return {
        id: typeof product.id === 'string' ? product.id : '',
        display_name: typeof product.display_name === 'string' ? product.display_name : undefined,
        name: typeof product.name === 'string' ? product.name : undefined,
        ...product,
      };
    }).filter(product => product.id),
  };
}
