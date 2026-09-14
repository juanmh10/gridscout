import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../api';
import { formatBRL, formatDateTime, formatPercent, translateCondition, canonicalSourceUrl } from '../lib/format';
import { getPipelineChat, sendPipelineChatMessage } from '../lib/pipelineChat';
import type { ChatMessage, PipelineChatActionName, PipelineChatCandidate, PipelineChatMessageRequest, PipelineChatMessageResponse, PipelineChatResponse } from '../lib/pipelineChat';
import { useProfile } from '../profile';

const CHAT_QUERY_KEY = 'pipeline-chat';

function makeRequestId(action: PipelineChatActionName): string {
  const randomUuid = typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `web-${action}-${randomUuid}`;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'PIPELINE_CHAT_UNAVAILABLE' || error.status === 409 && error.message.toLowerCase().includes('não está disponível')) return 'A análise não está disponível para esta execução.';
    if (error.code === 'MODEL_PROVIDER_DISABLED') return 'Esta ação exige o provedor de análise habilitado.';
    return error.message || 'Não foi possível concluir a análise.';
  }
  return 'Não foi possível concluir a análise agora.';
}

function numericValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value.replace(',', '.'));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatScore(value: unknown): string {
  const number = numericValue(value);
  return number === null ? '-' : number.toLocaleString('pt-BR', { maximumFractionDigits: 1 });
}

function formatMargin(value: unknown): string {
  const number = numericValue(value);
  return number === null ? '-' : formatPercent(Math.abs(number) > 1 ? number / 100 : number);
}

function candidateTitle(candidate: PipelineChatCandidate): string {
  return candidate.title || candidate.summary || 'Anúncio sem título';
}

function candidateProduct(candidate: PipelineChatCandidate): string {
  return candidate.product_name || candidate.product || 'Produto não identificado';
}

function candidateCondition(candidate: PipelineChatCandidate): string {
  return candidate.condition_assessment || translateCondition(candidate.condition || undefined);
}

function candidatePrice(candidate: PipelineChatCandidate): number | null {
  return numericValue(candidate.price ?? candidate.asking_price);
}

function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  const canonical = canonicalSourceUrl(value);
  try {
    const url = new URL(canonical);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function mergeMessageResponse(current: PipelineChatResponse | undefined, response: PipelineChatMessageResponse): PipelineChatResponse | undefined {
  if (!current) return current;
  const messages = response.message.id
    ? [...current.messages.filter(message => message.id !== response.message.id), response.message]
    : current.messages;
  return {
    ...current,
    thread: response.thread ?? current.thread,
    messages,
    available_actions: response.available_actions.length ? response.available_actions : current.available_actions,
    products: response.products.length ? response.products : current.products,
  };
}

function CandidateCard({ candidate }: { candidate: PipelineChatCandidate }) {
  const sourceUrl = safeExternalUrl(candidate.source_url);
  const price = candidatePrice(candidate);
  return (
    <article className="rounded-lg border border-slate-700 bg-slate-900 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0"><p className="font-medium text-slate-100">{candidateTitle(candidate)}</p><p className="mt-1 text-xs text-slate-400">{candidateProduct(candidate)}</p></div>
        {sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer noopener" referrerPolicy="no-referrer" className="shrink-0 text-xs text-sky-300 underline underline-offset-2">Abrir anúncio</a>}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
        <div><span className="block text-slate-500">Preço</span><span className="font-medium text-slate-200">{price === null ? '-' : formatBRL(price)}</span></div>
        <div><span className="block text-slate-500">Condição</span><span className="font-medium text-slate-200">{candidateCondition(candidate)}</span></div>
        <div><span className="block text-slate-500">Score</span><span className="font-medium text-slate-200">{formatScore(candidate.final_score ?? candidate.score)}</span></div>
        <div><span className="block text-slate-500">Margem</span><span className="font-medium text-slate-200">{formatMargin(candidate.price_edge ?? candidate.margin)}</span></div>
      </div>
      {candidate.reason && <p className="mt-3 border-t border-slate-800 pt-2 text-sm leading-6 text-slate-300">{candidate.reason}</p>}
    </article>
  );
}

function CitationList({ citations }: { citations: ChatMessage['citations'] }) {
  const safeCitations = citations.map(citation => ({ ...citation, url: safeExternalUrl(citation.url) })).filter(citation => citation.url);
  if (!safeCitations.length) return null;
  return (
    <div className="mt-3 border-t border-slate-700 pt-3">
      <p className="text-xs font-medium text-slate-400">Fontes consultadas</p>
      <ul className="mt-1 space-y-1">
        {safeCitations.map((citation, index) => <li key={`${citation.url}-${index}`}><a href={citation.url as string} target="_blank" rel="noreferrer noopener" referrerPolicy="no-referrer" className="text-xs text-sky-300 underline underline-offset-2">{citation.title || citation.url}</a></li>)}
      </ul>
    </div>
  );
}

function MessageBlock({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-3xl rounded-xl border px-4 py-3 ${isUser ? 'border-sky-500 bg-sky-600' : 'border-slate-700 bg-slate-800'}`}>
        <p className={`whitespace-pre-wrap text-sm leading-6 ${isUser ? 'text-white' : 'text-slate-100'}`}>{message.content}</p>
        {!isUser && message.candidates.length > 0 && <div className="mt-3 space-y-2">{message.candidates.slice(0, 10).map((candidate, index) => <CandidateCard key={`${candidate.id || candidate.listing_id || candidate.title || 'candidate'}-${index}`} candidate={candidate} />)}</div>}
        {!isUser && <CitationList citations={message.citations} />}
        {message.created_at && <p className={`mt-2 text-[11px] ${isUser ? 'text-sky-100' : 'text-slate-500'}`}>{formatDateTime(message.created_at)}</p>}
      </div>
    </div>
  );
}

export default function PipelineChat() {
  const { profileId, isManaged } = useProfile();
  const { pipelineId } = useParams<{ pipelineId: string }>();
  const queryClient = useQueryClient();
  const [text, setText] = useState('');
  const [requestError, setRequestError] = useState<string | null>(null);
  const overviewRequested = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messageCountRef = useRef<number | null>(null);
  const chatQueryKey = useMemo(() => [CHAT_QUERY_KEY, profileId, pipelineId], [pipelineId, profileId]);
  const chatQuery = useQuery({ queryKey: chatQueryKey, queryFn: () => getPipelineChat(pipelineId as string), enabled: Boolean(pipelineId) && (!isManaged || Boolean(profileId)) });
  const mutation = useMutation({
    mutationKey: ['pipeline-chat-message', profileId, pipelineId],
    mutationFn: (payload: PipelineChatMessageRequest) => sendPipelineChatMessage(pipelineId as string, payload),
    onMutate: () => setRequestError(null),
    onSuccess: async response => {
      queryClient.setQueryData<PipelineChatResponse>(chatQueryKey, current => mergeMessageResponse(current, response));
      await queryClient.invalidateQueries({ queryKey: chatQueryKey });
    },
    onError: (error: unknown) => setRequestError(errorText(error)),
  });

  useEffect(() => {
    const chat = chatQuery.data;
    if (!chat || !chat.eligibility.available || chat.thread || chat.messages.length || overviewRequested.current) return;
    if (!chat.available_actions.some(action => action.action === 'overview')) return;
    overviewRequested.current = true;
    mutation.mutate({ client_request_id: makeRequestId('overview'), action: 'overview' });
  }, [chatQuery.data, mutation]);

  useEffect(() => {
    const messageCount = chatQuery.data?.messages.length ?? 0;
    const shouldScroll = messageCountRef.current !== null && messageCount > messageCountRef.current;
    messageCountRef.current = messageCount;
    if (shouldScroll && typeof messagesEndRef.current?.scrollIntoView === 'function') messagesEndRef.current.scrollIntoView({ block: 'end' });
  }, [chatQuery.data?.messages.length]);

  function submitAction(action: PipelineChatActionName, actionText?: string) {
    if (mutation.isPending) return;
    mutation.mutate({ client_request_id: makeRequestId(action), action, ...(actionText ? { text: actionText } : {}) });
  }

  function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = text.trim();
    if (!question || mutation.isPending) return;
    setText('');
    submitAction('ask', question);
  }

  const chat = chatQuery.data;
  const availableActions = chat?.available_actions || [];
  const quickActions = availableActions.filter(action => action.action !== 'ask');
  const canAsk = availableActions.some(action => action.action === 'ask');
  const nextOffset = chat?.thread?.next_offset ?? 0;
  const totalCandidates = chat?.thread?.total_candidates ?? 0;

  return (
    <div className="h-full bg-slate-950 p-3 sm:p-6">
      <section className="mx-auto flex h-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl shadow-black/20">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-4 sm:px-6">
          <div className="min-w-0"><Link to="/pipelines" className="text-sm text-sky-300 underline underline-offset-2">Voltar aos pipelines</Link><h1 className="mt-2 text-lg font-semibold text-white">Análise desta execução</h1></div>
          {chat?.thread && totalCandidates > 0 && <p className="text-sm text-slate-400">{Math.min(nextOffset, totalCandidates)} de {totalCandidates} anúncios apresentados</p>}
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          {chatQuery.isLoading && <div className="text-sm text-slate-400">Carregando análise...</div>}
          {chatQuery.isError && <div role="alert" className="rounded-lg border border-red-900 bg-red-950/40 p-4 text-sm text-red-200">{errorText(chatQuery.error)}</div>}
          {chat && !chat.eligibility.available && <div role="alert" className="rounded-lg border border-amber-800 bg-amber-950/40 p-4 text-sm text-amber-100">{chat.eligibility.reason_message || 'A análise não está disponível para esta execução.'}</div>}
          {chat?.eligibility.available && <div className="space-y-4">{chat.messages.map(message => <MessageBlock key={message.id} message={message} />)}{mutation.isPending && <div className="w-fit rounded-xl border border-slate-700 bg-slate-800 px-4 py-3 text-sm text-slate-400">Analisando...</div>}{requestError && <div role="alert" className="rounded-lg border border-amber-800 bg-amber-950/40 p-3 text-sm text-amber-100">{requestError}</div>}<div ref={messagesEndRef} /></div>}
        </div>
        {chat?.eligibility.available && (
          <footer className="border-t border-slate-800 bg-slate-900 px-4 py-4 sm:px-6">
            <div className="flex flex-wrap gap-2">{quickActions.map(action => <button key={action.action} type="button" disabled={mutation.isPending} onClick={() => submitAction(action.action as PipelineChatActionName)} className="rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-slate-500 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50">{action.label || action.action}</button>)}</div>
            {canAsk ? (
              <form onSubmit={submitQuestion} className="mt-3 flex gap-2"><label htmlFor="pipeline-chat-question" className="sr-only">Pergunta sobre o pipeline</label><input id="pipeline-chat-question" value={text} onChange={event => setText(event.target.value)} placeholder="Pergunte sobre esta execução" disabled={mutation.isPending} className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-sky-400 disabled:opacity-60" /><button type="submit" disabled={mutation.isPending || !text.trim()} className="rounded-md bg-sky-500 px-4 py-2 text-sm font-medium text-slate-950 transition-colors hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-50">Enviar</button></form>
            ) : <p className="mt-3 text-xs text-slate-500">Perguntas livres e comparação externa exigem o provedor de análise habilitado.</p>}
          </footer>
        )}
      </section>
    </div>
  );
}
