import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Check, Clock3, Database, Edit3, Heart, Loader2, Plus, Send, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import {
  createSearchSession,
  getSearchSession,
  listSearchDefinitions,
  listSearchSessions,
  saveSearchDraft,
  saveSearchSession,
  sendSearchMessage,
} from '../api';
import { translateCategory } from '../lib/format';
import { useProfile } from '../profile';

type JsonRecord = Record<string, any>;

function items(payload: unknown): JsonRecord[] {
  if (Array.isArray(payload)) return payload as JsonRecord[];
  if (!payload || typeof payload !== 'object') return [];
  const value = payload as JsonRecord;
  for (const key of ['items', 'sessions', 'definitions']) if (Array.isArray(value[key])) return value[key];
  return [];
}

function sessionBody(payload: unknown): JsonRecord | null {
  if (!payload || typeof payload !== 'object') return null;
  const value = payload as JsonRecord;
  return value.session && typeof value.session === 'object' ? value.session : value;
}

function idOf(value: JsonRecord | undefined | null): string { return String(value?.id || value?.session_id || ''); }
function textOf(value: unknown): string { return typeof value === 'string' ? value : value == null ? '' : String(value); }
function dateOf(value: unknown): string { const date = new Date(textOf(value)); return Number.isNaN(date.valueOf()) ? '' : date.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }); }
function requestId(): string { const uuid = typeof globalThis.crypto?.randomUUID === 'function' ? globalThis.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`; return `web-search-${uuid}`; }
function money(value: unknown): string { const number = Number(value); return Number.isFinite(number) ? `R$ ${number.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}` : ''; }

function criterionLabel(criterion: JsonRecord): string {
  const labels: Record<string, string> = { category: 'Categoria', brand: 'Marca', family: 'Família', model: 'Modelo', generation: 'Geração', panel_type: 'Tela', wifi_bands: 'Wi-Fi', ram_gb: 'RAM', ssd_gb: 'SSD', price: 'Preço', location: 'Local', condition: 'Condição' };
  const field = textOf(criterion.field);
  let value = Array.isArray(criterion.value) ? criterion.value.join(', ') : textOf(criterion.value);
  if (field === 'price' && criterion.unit === 'BRL') value = money(criterion.value);
  if (field.endsWith('_gb')) value = `${value} GB`;
  return `${labels[field] || field}: ${value}`.trim();
}

function criterionMeaning(criterion: JsonRecord): string {
  const field = textOf(criterion.field);
  const value = Array.isArray(criterion.value) ? criterion.value.join(', ') : textOf(criterion.value);
  if (field === 'category') return `Produto: ${value}`;
  if (field === 'panel_type') return `Tela: ${value} (painel identificado no anúncio)`;
  if (field === 'wifi_bands') return `Wi-Fi: ${value.replace('GHz', ' GHz')} (suporte declarado)`;
  if (field === 'price') return `Preço máximo: ${money(criterion.value) || value}`;
  if (field === 'location') return `Local: ${value}`;
  if (field === 'ram_gb') return `Memória: ${value} GB`;
  if (field === 'ssd_gb') return `SSD: ${value} GB`;
  return criterionLabel(criterion);
}

function sessionModelTitle(session: JsonRecord): string {
  if (session.saved_definition_name && typeof session.saved_definition_name === 'string') {
    return session.saved_definition_name.trim();
  }

  const draft = (session.draft || {}) as JsonRecord;
  const plan = (session.plan || draft.plan || {}) as JsonRecord;
  const intent = (plan.intent || session.intent || draft.intent || {}) as JsonRecord;

  const parts: string[] = [];

  const productValue = (
    (Array.isArray(intent.models) && intent.models[0]) ||
    (Array.isArray(intent.families) && intent.families[0]) ||
    (Array.isArray(intent.brands) && intent.brands[0]) ||
    intent.category ||
    ''
  );
  const rawProduct = textOf(productValue).trim();
  if (rawProduct) {
    parts.push(translateCategory(rawProduct));
  }

  const must = (Array.isArray(plan.must) ? plan.must : Array.isArray(intent.must) ? intent.must : []) as JsonRecord[];
  const fieldLabels: Record<string, string> = {
    panel_type: 'Tela',
    wifi_bands: 'Wi-Fi',
    ram_gb: 'RAM',
    ssd_gb: 'SSD',
  };

  for (const crit of must) {
    if (!crit || typeof crit !== 'object') continue;
    const field = textOf(crit.field);
    if (fieldLabels[field] && crit.value != null && crit.value !== '') {
      const val = Array.isArray(crit.value) ? crit.value.join(', ') : textOf(crit.value);
      if (field.endsWith('_gb')) {
        parts.push(`${val} GB ${fieldLabels[field]}`);
      } else {
        parts.push(`${fieldLabels[field]} ${val}`);
      }
    }
  }

  const budget = (intent.budget || {}) as JsonRecord;
  const maxPrice = Number(budget.max != null ? budget.max : must.find(c => textOf(c.field) === 'price')?.value);
  if (Number.isFinite(maxPrice) && maxPrice > 0) {
    parts.push(`até R$ ${maxPrice.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}`);
  }

  if (parts.length > 0) {
    return parts.join(' · ');
  }

  const rawQuery = textOf(intent.raw_query || intent.query || session.name || '');
  return rawQuery || 'Nova busca';
}

function ScopeUnderstanding({ session, onChange, disabled }: { session: JsonRecord; onChange: (plan: JsonRecord) => void; disabled?: boolean }) {
  const plan = (session.plan || {}) as JsonRecord;
  const groups: Array<{ key: 'must' | 'should' | 'must_not'; title: string; tone: string }> = [
    { key: 'must', title: 'Obrigatórios', tone: 'border-sky-400/25 bg-sky-400/5' },
    { key: 'should', title: 'Desejáveis', tone: 'border-slate-700 bg-slate-900/60' },
    { key: 'must_not', title: 'Excluir', tone: 'border-rose-400/25 bg-rose-400/5' },
  ];
  const updateGroup = (group: 'must' | 'should' | 'must_not', index: number, target?: 'must' | 'should') => {
    const next = { ...plan, must: [...(plan.must || [])], should: [...(plan.should || [])], must_not: [...(plan.must_not || [])] };
    const [criterion] = next[group].splice(index, 1);
    if (criterion && target) next[target].push(criterion);
    onChange(next);
  };
  const required = (plan.must || []) as JsonRecord[];
  const primaryQueries = Array.isArray(plan.primary_queries) ? plan.primary_queries.map(textOf).filter(Boolean) : [];
  const fallbackQueries = Array.isArray(plan.fallback_queries) ? plan.fallback_queries.map(textOf).filter(Boolean) : [];
  const marketplaceReview = Array.isArray(plan.marketplace_review) ? plan.marketplace_review as JsonRecord[] : [];
  const intent = (plan.intent || session.intent || {}) as JsonRecord;
  const isNotebook = intent.category === 'notebook' || plan.catalog_match === 'notebook-brasil-v1';
  const useDataset = plan.use_dataset_match !== false;

  if (!groups.some(group => Array.isArray(plan[group.key]) && plan[group.key].length)) return null;
  return <section className="rounded-2xl border border-slate-700 bg-slate-900/90 p-4 shadow-xl shadow-black/10">
    <div><p className="text-sm font-semibold text-white">Entendimento da busca</p><p className="mt-1 text-xs leading-5 text-slate-400">O pipeline só confirma um anúncio quando houver evidência para todos os requisitos obrigatórios.</p></div>
    {required.length > 0 && <div className="mt-4 rounded-xl border border-sky-400/20 bg-sky-400/5 p-3"><p className="text-[11px] font-medium uppercase tracking-wider text-sky-300">O que será procurado</p><ul className="mt-2 space-y-1.5 text-sm text-slate-200">{required.map((criterion, index) => { const review = marketplaceReview.find(item => textOf(item.field) === textOf(criterion.field)); return <li key={`meaning-${index}`}><span>{review?.meaning || criterionMeaning(criterion)}</span><span className="ml-2 text-xs text-slate-400">{review?.evidence_source === 'detail' ? 'página do anúncio' : 'card ou anúncio'} · ausente = não verificado</span></li>; })}</ul></div>}
    {isNotebook && (
      <div className="mt-3 rounded-xl border border-blue-500/25 bg-blue-500/10 p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Database size={15} className="text-blue-400" />
            <p className="text-xs font-semibold text-blue-200">Match com Dataset de Catálogo</p>
          </div>
          {!disabled && (
            <button
              type="button"
              onClick={() => {
                const nextUse = !useDataset;
                onChange({
                  ...plan,
                  use_dataset_match: nextUse,
                  catalog_match: nextUse ? 'notebook-brasil-v1' : null,
                });
              }}
              className={`rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors ${
                useDataset
                  ? 'bg-blue-600 text-white hover:bg-blue-500'
                  : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
              }`}
            >
              {useDataset ? 'Ativo' : 'Desativado'}
            </button>
          )}
        </div>
        <p className="mt-1 text-xs leading-4 text-slate-300">
          {useDataset
            ? 'Dataset Brasil ativo: cruzamento com base técnica de 348+ modelos para confirmação de especificações (telas IPS, processador e memória) a partir de buscas amplas no marketplace.'
            : 'Busca direta em anúncios (validação baseada apenas no texto do anúncio sem consultar o dataset de notebooks).'}
        </p>
      </div>
    )}

    {/* Filtros e Toggles de Busca (Preço e OLX Pay) */}
    <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
      <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3 flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-slate-200">Apenas com preço</p>
          <p className="text-[11px] text-slate-400 leading-tight">Descarta anúncios 'a combinar' ou sem valor</p>
        </div>
        {!disabled && (
          <button
            type="button"
            onClick={() => onChange({ ...plan, require_price: !(plan.require_price !== false) })}
            className={`rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors ${
              plan.require_price !== false ? 'bg-sky-500 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
            }`}
          >
            {plan.require_price !== false ? 'Ativo' : 'Inativo'}
          </button>
        )}
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3 flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-slate-200">Garantia OLX / Entrega</p>
          <p className="text-[11px] text-slate-400 leading-tight">Filtra anúncios com OLX Pay e envio</p>
        </div>
        {!disabled && (
          <button
            type="button"
            onClick={() => onChange({ ...plan, olx_pay_only: !plan.olx_pay_only })}
            className={`rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors ${
              plan.olx_pay_only ? 'bg-sky-500 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
            }`}
          >
            {plan.olx_pay_only ? 'Ativo' : 'Inativo'}
          </button>
        )}
      </div>
    </div>
    <div className="mt-4 space-y-3">{groups.map(group => { const values = (plan[group.key] || []) as JsonRecord[]; if (!values.length) return null; return <div key={group.key} className={`rounded-xl border p-3 ${group.tone}`}><p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-400">{group.title}</p><div className="flex flex-wrap gap-2">{values.map((criterion, index) => <span key={`${group.key}-${index}`} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-950/70 px-2.5 py-1.5 text-xs text-slate-200">{criterionLabel(criterion)}{!disabled && <>{group.key === 'must' && <button type="button" title="Tornar desejável" onClick={() => updateGroup('must', index, 'should')} className="ml-1 text-slate-500 hover:text-sky-300"><Edit3 size={12} /></button>}{group.key === 'should' && <button type="button" title="Tornar obrigatório" onClick={() => updateGroup('should', index, 'must')} className="ml-1 text-slate-500 hover:text-sky-300"><Check size={12} /></button>}<button type="button" title="Remover critério" onClick={() => updateGroup(group.key, index)} className="text-slate-500 hover:text-rose-300"><X size={12} /></button></>}</span>)}</div></div>; })}</div>
    {primaryQueries.length > 0 && <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs leading-5 text-slate-400"><p className="font-medium text-slate-300">Consulta na OLX</p><p className="mt-1">Busca inicial: <span className="font-mono text-slate-200">{primaryQueries.join(' · ')}</span></p>{fallbackQueries.length > 0 && <p>Ampliação se necessário: <span className="font-mono text-slate-200">{fallbackQueries.join(' · ')}</span></p>}</div>}
    {!!(session.clarification?.message || plan.clarifications?.length) && <div className="mt-3 rounded-lg border border-amber-400/25 bg-amber-400/10 p-3 text-xs leading-5 text-amber-100">{session.clarification?.message || plan.clarifications?.[0]}</div>}
  </section>;
}

export default function Searches() {
  const { profileId, isManaged } = useProfile();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const enabled = !isManaged || Boolean(profileId);
  const [selectedId, setSelectedId] = useState('');
  const [localSession, setLocalSession] = useState<JsonRecord | null>(null);
  const [message, setMessage] = useState('');
  const [sidebarTab, setSidebarTab] = useState<'history' | 'saved'>('history');
  const selectionInitialized = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const sessionsQuery = useQuery({ queryKey: ['search-sessions', profileId], queryFn: listSearchSessions, enabled });
  const definitionsQuery = useQuery({ queryKey: ['search-definitions', profileId], queryFn: listSearchDefinitions, enabled });
  const sessionList = useMemo(() => items(sessionsQuery.data), [sessionsQuery.data]);
  const definitionList = useMemo(() => items(definitionsQuery.data), [definitionsQuery.data]);
  const listSession = sessionList.find(item => idOf(item) === selectedId);
  const needsDetail = Boolean(selectedId && !localSession && !listSession?.messages);
  const sessionDetailQuery = useQuery({ queryKey: ['search-session', profileId, selectedId], queryFn: () => getSearchSession(selectedId), enabled: enabled && needsDetail });
  const session = localSession || sessionBody(sessionDetailQuery.data) || listSession || {};
  const sessionId = idOf(session) || selectedId;
  const plan = (session.plan || {}) as JsonRecord;
  const messages = (session.messages || []) as JsonRecord[];

  useEffect(() => {
    if (!selectionInitialized.current && !selectedId && sessionList[0]) {
      setSelectedId(idOf(sessionList[0]));
      selectionInitialized.current = true;
    }
  }, [selectedId, sessionList]);
  useEffect(() => { if (sessionDetailQuery.data) setLocalSession(sessionBody(sessionDetailQuery.data)); }, [sessionDetailQuery.data]);
  useEffect(() => { bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' }); }, [messages.length]);
  useEffect(() => { selectionInitialized.current = false; setSelectedId(''); setLocalSession(null); }, [profileId]);

  const invalidate = () => { void queryClient.invalidateQueries({ queryKey: ['search-sessions', profileId] }); void queryClient.invalidateQueries({ queryKey: ['search-definitions', profileId] }); };
  const setSession = (payload: unknown) => {
    const next = sessionBody(payload);
    if (!next) return;
    setLocalSession(previous => ({ ...session, ...(previous || {}), ...next }));
  };
  const messageMutation = useMutation({ mutationFn: (content: string) => sendSearchMessage(sessionId, { client_request_id: requestId(), content }), onSuccess: payload => { setSession(payload); setMessage(''); invalidate(); }, onError: () => toast.error('Não foi possível atualizar o escopo.') });
  const createMutation = useMutation({ mutationFn: (request: string | JsonRecord) => createSearchSession(typeof request === 'string' ? { text: request } : request), onSuccess: payload => { const created = sessionBody(payload); setLocalSession(created); setSelectedId(idOf(created)); setMessage(''); invalidate(); }, onError: () => toast.error('Não foi possível iniciar a busca.') });
  const draftMutation = useMutation({ mutationFn: (nextPlan: JsonRecord) => saveSearchDraft(sessionId, { plan: nextPlan }), onSuccess: payload => { setSession(payload); toast.success('Escopo atualizado.'); invalidate(); }, onError: () => toast.error('Esse ajuste não pôde ser aplicado.') });
  const saveMutation = useMutation({ mutationFn: () => saveSearchSession(sessionId), onSuccess: payload => { setSession((payload as JsonRecord).session || payload); toast.success('Escopo salvo.'); invalidate(); }, onError: () => toast.error('Não foi possível salvar esse escopo.') });
  const prepareMutation = useMutation({
    mutationFn: async () => {
      if (session.search_definition_id) return session.search_definition_id as string;
      const payload = await saveSearchSession(sessionId) as JsonRecord;
      return textOf(payload.definition?.id || payload.session?.search_definition_id);
    },
    onSuccess: definitionId => {
      if (!definitionId) { toast.error('Não foi possível preparar esse escopo.'); return; }
      toast.success('Escopo preparado no Pipeline.');
      navigate(`/pipelines?search_definition=${encodeURIComponent(definitionId)}`);
    },
    onError: () => toast.error('Não foi possível preparar o Pipeline.'),
  });

  function submit(event?: FormEvent) { event?.preventDefault(); const value = message.trim(); if (!value || messageMutation.isPending || createMutation.isPending) return; if (!sessionId) createMutation.mutate(value); else messageMutation.mutate(value); }
  function newSearch() { selectionInitialized.current = true; setSelectedId(''); setLocalSession(null); setMessage(''); }
  function selectSession(value: JsonRecord) { selectionInitialized.current = true; setSelectedId(idOf(value)); setLocalSession(Array.isArray(value.messages) ? value : null); }
  function openDefinition(definition: JsonRecord) { createMutation.mutate({ search_definition_id: idOf(definition) }); }
  const canSave = Boolean(sessionId && Object.keys(plan).length > 0 && !session.clarification?.message);

  const duplicateMatch = useMemo(() => {
    if (!sessionId || !session || session.is_saved) return null;
    const currentTitle = sessionModelTitle(session).toLowerCase().trim();
    const currentQuery = String(session.intent?.query || session.intent?.raw_query || '').toLowerCase().trim();
    if (!currentTitle && !currentQuery) return null;

    return definitionList.find((def: JsonRecord) => {
      const defId = idOf(def);
      if (session.search_definition_id && defId === session.search_definition_id) return false;
      const defName = textOf(def.name).toLowerCase().trim();
      const defQuery = textOf(def.intent?.query || def.intent?.raw_query || '').toLowerCase().trim();
      if (defName && (defName === currentTitle || (currentTitle.length > 3 && defName.includes(currentTitle)) || (defName.length > 3 && currentTitle.includes(defName)))) return true;
      if (defQuery && currentQuery && (defQuery === currentQuery || (currentQuery.length > 3 && defQuery.includes(currentQuery)))) return true;
      return false;
    });
  }, [sessionId, session, definitionList]);

  return (
    <div className="flex h-full overflow-hidden bg-slate-950 text-slate-100">
      {/* Área central do chat */}
      <section className="flex flex-1 flex-col h-full overflow-hidden bg-slate-950 min-w-0">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3.5 sm:px-6">
          <h1 className="text-lg font-semibold text-white">Buscas</h1>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={newSearch}
              className="inline-flex items-center gap-1.5 rounded-lg bg-sky-400 px-3 py-2 text-xs font-semibold text-slate-950 transition-colors hover:bg-sky-300"
            >
              <Plus size={15} /> Nova busca
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          <div className="mx-auto max-w-3xl space-y-4">
            {!messages.length && !sessionId && <div className="min-h-[55vh]" aria-hidden="true" />}
            {messages.map((item, index) => (
              <div key={String(item.id || index)} className={`flex ${item.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[88%] rounded-2xl border px-4 py-3 text-sm leading-6 ${item.role === 'user' ? 'border-sky-400/40 bg-sky-500 text-white' : 'border-slate-700 bg-slate-900 text-slate-200'}`}>
                  <p className="whitespace-pre-wrap">{textOf(item.content || item.message || item.text)}</p>
                </div>
              </div>
            ))}
            {(messageMutation.isPending || createMutation.isPending) && (
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <Loader2 size={14} className="animate-spin" /> Atualizando escopo...
              </div>
            )}
            {duplicateMatch && (
              <div className="rounded-xl border border-amber-400/30 bg-amber-400/10 p-3.5 text-xs text-amber-200 flex flex-wrap items-center justify-between gap-3">
                <div className="flex-1 min-w-[240px]">
                  <p className="font-semibold text-amber-100 flex items-center gap-1.5">
                    <AlertTriangle size={15} className="text-amber-400" /> Escopo salvo semelhante encontrado
                  </p>
                  <p className="mt-1 text-amber-200/90 leading-5">
                    Você já possui o escopo <strong>{textOf(duplicateMatch.name)}</strong> salvo neste perfil. Reutilizá-lo evita coletas duplicadas de páginas na OLX.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => openDefinition(duplicateMatch)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-amber-400/20 border border-amber-400/40 px-3 py-1.5 text-xs font-semibold text-amber-100 hover:bg-amber-400/30 transition-colors"
                >
                  Reutilizar escopo salvo
                </button>
              </div>
            )}
            {sessionId && (
              <ScopeUnderstanding
                session={session}
                onChange={nextPlan => draftMutation.mutate(nextPlan)}
                disabled={draftMutation.isPending || prepareMutation.isPending}
              />
            )}
            {sessionId && Object.keys(plan).length > 0 && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3">
                <span className="text-xs text-slate-400">
                  {session.is_saved && !session.has_unsaved_changes
                    ? 'Escopo salvo'
                    : session.has_unsaved_changes
                      ? 'Alterações não salvas'
                      : 'Pronto para salvar'}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => saveMutation.mutate()}
                    disabled={!canSave || saveMutation.isPending}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:border-slate-500 disabled:opacity-50"
                  >
                    <Heart size={14} className={session.is_saved ? 'fill-rose-400 text-rose-400' : ''} />
                    {saveMutation.isPending ? 'Salvando...' : session.is_saved && !session.has_unsaved_changes ? 'Salvo' : 'Salvar escopo'}
                  </button>
                  <button
                    type="button"
                    onClick={() => prepareMutation.mutate()}
                    disabled={!canSave || prepareMutation.isPending}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-sky-400 px-3 py-2 text-xs font-semibold text-slate-950 transition-colors hover:bg-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Send size={14} />
                    {prepareMutation.isPending ? 'Preparando...' : 'Abrir no Pipeline'}
                  </button>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </main>

        <footer className="border-t border-slate-800 bg-slate-950 px-4 py-4 sm:px-6">
          <form onSubmit={submit} className="mx-auto flex max-w-3xl items-end gap-2">
            <label htmlFor="search-chat-message" className="sr-only">Mensagem da busca</label>
            <textarea
              id="search-chat-message"
              rows={1}
              value={message}
              onChange={event => setMessage(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              placeholder="Ex.: notebook IPS, Wi-Fi 5 GHz, até R$ 2.500"
              disabled={messageMutation.isPending || createMutation.isPending}
              className="min-h-11 flex-1 resize-none rounded-xl border border-slate-700 bg-slate-900 px-3.5 py-3 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-sky-400 disabled:opacity-60"
            />
            <button
              type="submit"
              aria-label="Enviar"
              disabled={!message.trim() || messageMutation.isPending || createMutation.isPending}
              className="flex h-11 w-11 items-center justify-center rounded-xl bg-sky-400 text-slate-950 transition-colors hover:bg-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={17} />
            </button>
          </form>
        </footer>
      </section>

      {/* Histórico na direita fora do chat */}
      <aside
        aria-label="Histórico de buscas"
        className="flex h-full w-72 sm:w-80 shrink-0 flex-col overflow-hidden border-l border-slate-800 bg-slate-900/60"
      >
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3 bg-slate-900/90">
          <div className="flex items-center gap-2">
            <Clock3 size={16} className="text-slate-400" />
            <h2 className="text-sm font-semibold text-slate-200">Histórico</h2>
          </div>
          <div className="flex items-center rounded-lg bg-slate-950 p-0.5 border border-slate-800">
            <button
              type="button"
              onClick={() => setSidebarTab('history')}
              className={`rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                sidebarTab === 'history'
                  ? 'bg-slate-800 text-sky-400 font-semibold'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Chats{sessionList.length > 0 ? ` (${sessionList.length})` : ''}
            </button>
            <button
              type="button"
              onClick={() => setSidebarTab('saved')}
              className={`rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                sidebarTab === 'saved'
                  ? 'bg-slate-800 text-sky-400 font-semibold'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Salvos{definitionList.length > 0 ? ` (${definitionList.length})` : ''}
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1 custom-scrollbar">
          {sidebarTab === 'history' ? (
            sessionsQuery.isLoading ? (
              <p className="p-3 text-xs text-slate-500">Carregando histórico...</p>
            ) : sessionList.length === 0 ? (
              <p className="p-3 text-xs text-slate-500">Nenhum chat no histórico.</p>
            ) : (
              sessionList.map(item => {
                const id = idOf(item);
                const isSelected = id === sessionId;
                const title = sessionModelTitle(item);
                const date = dateOf(item.updated_at || item.created_at);
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => selectSession(item)}
                    title={title}
                    className={`group flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 text-left transition-colors ${
                      isSelected
                        ? 'bg-slate-800 text-white ring-1 ring-slate-700'
                        : 'text-slate-300 hover:bg-slate-800/60 hover:text-slate-100'
                    }`}
                  >
                    <span className="truncate block w-full text-xs font-medium leading-5">
                      {title}
                    </span>
                    <span className="text-[10px] text-slate-500">
                      {date}
                    </span>
                  </button>
                );
              })
            )
          ) : (
            definitionsQuery.isLoading ? (
              <p className="p-3 text-xs text-slate-500">Carregando escopos salvos...</p>
            ) : definitionList.length === 0 ? (
              <p className="p-3 text-xs text-slate-500">Nenhum escopo salvo.</p>
            ) : (
              definitionList.map(item => {
                const id = idOf(item);
                const name = textOf(item.name || 'Escopo salvo');
                const date = dateOf(item.updated_at || item.created_at);
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => openDefinition(item)}
                    title={name}
                    className="group flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 text-left text-slate-300 transition-colors hover:bg-slate-800/60 hover:text-slate-100"
                  >
                    <span className="truncate block w-full text-xs font-medium leading-5">
                      {name}
                    </span>
                    <span className="text-[10px] text-slate-500">
                      {date}
                    </span>
                  </button>
                );
              })
            )
          )}
        </div>
      </aside>
    </div>
  );
}
