import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronDown, Loader2, Play, Search, Square } from 'lucide-react';
import { toast } from 'sonner';
import { fetcher, getApiErrorDetail } from '../api';
import { formatDateTime, translateStatus } from '../lib/format';
import { useProfile } from '../profile';

const PIPELINE_PAGE_SIZE = 20;
const RECENT_PIPELINE_COUNT = 3;
type SourceType = 'fixture' | 'olx';
type RunMode = 'definition' | 'manual' | 'adhoc';
type WorkloadMode = 'standard' | 'high_volume';
type Aggressiveness = 'conservative' | 'balanced' | 'intensive';
type JsonRecord = Record<string, any>;

interface OlxRateLimit {
  state: 'closed' | 'cooldown';
  limit_per_hour: number;
  used: number;
  remaining: number;
  reset_at: string | null;
  retry_after_seconds: number | null;
}

interface HighVolumePreflight {
  state: 'ready' | 'waiting_budget' | 'unavailable';
  can_start: boolean;
  message?: string;
  duration_minutes: number;
  aggressiveness: Aggressiveness;
  pace_seconds: number;
  navigation_cap: number;
  discovery_navigation_cap: number;
  navigation_reserve: number;
  estimated_discoveries: { min: number; max: number };
  estimated_agent_reviews?: { min: number; max: number };
  estimated_details_max: number;
  estimate_basis: string;
  required_to_start: number;
  remaining?: number;
  retry_after_seconds?: number | null;
  card_agent_available: boolean;
  estimated_agent_reviews_max: number;
  card_review_mode?: 'disabled' | 'required';
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value);
}

function money(value: unknown): string {
  const amount = Number(value);
  return Number.isFinite(amount) ? `R$ ${amount.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}` : '';
}

function criterionText(criterion: JsonRecord): string {
  const labels: Record<string, string> = {
    category: 'Categoria', brand: 'Marca', family: 'Família', model: 'Modelo', generation: 'Geração',
    panel_type: 'Tela', wifi_bands: 'Wi-Fi', ram_gb: 'RAM', ssd_gb: 'SSD', price: 'Preço', location: 'Local', condition: 'Condição',
  };
  const field = text(criterion.field);
  let value = Array.isArray(criterion.value) ? criterion.value.join(', ') : text(criterion.value);
  if (field === 'price') value = money(criterion.value) || value;
  if (field.endsWith('_gb')) value = `${value} GB`;
  return `${labels[field] || field}: ${value}`;
}

function definitionSummary(definition?: JsonRecord | null): string[] {
  if (!definition) return [];
  const plan = (definition.plan || {}) as JsonRecord;
  return ((plan.must || []) as JsonRecord[]).slice(0, 4).map(criterionText);
}

function boundedLimit(value: unknown, fallback: number): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(1, Math.min(Math.floor(numeric), 30));
}

function boundedDuration(value: unknown, fallback = 180): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(5, Math.min(Math.floor(numeric), 180));
}

function aggressivenessLabel(value: unknown): string {
  const labels: Record<string, string> = {
    conservative: 'Conservador',
    balanced: 'Equilibrado',
    intensive: 'Intensivo',
  };
  return labels[text(value)] || 'Equilibrado';
}

function discoveryEstimateLabel(value: unknown): string {
  const estimate = (value || {}) as JsonRecord;
  const min = Number(estimate.min);
  const max = Number(estimate.max);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return 'previsão indisponível';
  if (min === max) return `aprox. ${min.toLocaleString('pt-BR')} descobertas`;
  return `aprox. ${min.toLocaleString('pt-BR')}–${max.toLocaleString('pt-BR')} descobertas`;
}

function highVolumeForecastLabel(forecast?: HighVolumePreflight): string {
  if (!forecast || !Number.isFinite(Number(forecast.estimated_details_max))) return 'previsão indisponível';
  const discoveries = discoveryEstimateLabel(forecast.estimated_discoveries);
  if (discoveries === 'previsão indisponível') return discoveries;
  const reviews = forecast.card_review_mode === 'required' && forecast.estimated_agent_reviews
    ? ` · agente: ${discoveryEstimateLabel(forecast.estimated_agent_reviews).replace(' descobertas', '')}`
    : '';
  return `${discoveries}${reviews} · até ${Number(forecast.estimated_details_max)} detalhes`;
}

function definitionNavigationEstimate(definition: JsonRecord, fallbackLimit: number): number {
  const plan = (definition.plan || {}) as JsonRecord;
  const queryValues = [
    ...(Array.isArray(plan.primary_queries) ? plan.primary_queries : []),
    ...(Array.isArray(plan.fallback_queries) ? plan.fallback_queries : []),
  ].map(text).filter(Boolean);
  const candidateCap = boundedLimit(plan.max_results, boundedLimit(Number(plan.desired_count) * 3, fallbackLimit));
  const queryCount = Math.min(new Set(queryValues.map(value => value.toLocaleLowerCase('pt-BR'))).size || 1, 6, candidateCap);
  return 1 + queryCount + candidateCap;
}

function waitLabel(value: unknown): string {
  const seconds = Math.max(0, Math.round(Number(value) || 0));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes <= 0) return `${remainder}s`;
  if (minutes < 60) return remainder ? `${minutes} min ${remainder}s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60} min`;
}

function PulseDot({ dark = false }: { dark?: boolean }) {
  return (
    <span className="relative flex h-2.5 w-2.5 shrink-0" aria-hidden="true">
      <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${dark ? 'bg-black opacity-60' : 'bg-blue-600 opacity-75'}`} />
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${dark ? 'bg-black' : 'bg-blue-600'}`} />
    </span>
  );
}

function ShimmerBar({ isSky = false }: { isSky?: boolean }) {
  return (
    <div className={`absolute inset-x-0 top-0 h-1 overflow-hidden ${isSky ? 'bg-sky-200' : 'bg-blue-100'}`}>
      <div className={`h-full w-full animate-pulse ${isSky ? 'bg-black' : 'bg-blue-600'}`} />
    </div>
  );
}

function isExecutingStatus(status: string): boolean {
  return ['running', 'pending', 'queued', 'waiting_budget', 'claimed', 'waiting_worker', 'reviewing_cards'].includes(status);
}

function parseUtcTimestamp(value: unknown): number | null {
  if (!value) return null;
  const raw = String(value).trim();
  const dateStr = raw.endsWith('Z') || raw.includes('+') || (raw.includes('-') && raw.lastIndexOf('-') > 10) ? raw : `${raw}Z`;
  const ms = new Date(dateStr).getTime();
  return isNaN(ms) ? null : ms;
}

function PipelineRunCard({
  pipe,
  isRunningSection = false,
}: {
  pipe: JsonRecord;
  isRunningSection?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(true);
  const isExecuting = isExecutingStatus(pipe.status);
  const terminal = ['completed', 'completed_partial', 'failed', 'blocked', 'cancelled'].includes(pipe.status);
  const chat = pipe.chat || { available: pipe.status === 'completed' };
  const chatAvailable = chat.available === true;

  const [liveDuration, setLiveDuration] = useState<number>(Number(pipe.duration_seconds) || 0);

  useEffect(() => {
    if (!isExecuting || !pipe.started_at) {
      setLiveDuration(Number(pipe.duration_seconds) || 0);
      return;
    }
    const startMs = parseUtcTimestamp(pipe.started_at);
    if (!startMs) return;

    const updateTimer = () => {
      const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
      setLiveDuration(elapsedSeconds);
    };

    updateTimer();
    const interval = setInterval(updateTimer, 1000);
    return () => clearInterval(interval);
  }, [isExecuting, pipe.duration_seconds, pipe.started_at]);

  const configuration = (pipe.steps?.[0]?.configuration || {}) as JsonRecord;
  const label = text(configuration.search_definition_name) || 'Execução de pipeline';
  const workload = (pipe.workload || {}) as JsonRecord;
  const datasetAnalysis = (pipe.dataset_analysis || {}) as JsonRecord;
  const datasetObserved = (datasetAnalysis.observed || {}) as JsonRecord;
  const datasetQuality = (datasetAnalysis.capture_quality || {}) as JsonRecord;
  const datasetPriceCoverage = Number((datasetQuality.price_usable || {}).coverage || 0);
  const datasetReady = datasetAnalysis.status === 'completed';

  const queryClient = useQueryClient();
  const resumeMutation = useMutation({
    mutationFn: () => fetcher(`/pipelines/${encodeURIComponent(pipe.id)}/resume`, { method: 'POST' }),
    onSuccess: () => {
      toast.success('Retomando detalhes pendentes.');
      void queryClient.invalidateQueries({ queryKey: ['pipelines'] });
    },
    onError: (error: any) => {
      toast.error(error?.message || 'Falha ao retomar detalhes.');
    },
  });

  const cancelMutation = useMutation({
    mutationFn: () => fetcher(`/pipelines/${encodeURIComponent(pipe.id)}/cancel`, { method: 'POST' }),
    onSuccess: () => {
      toast.success('Pipeline interrompido.');
      void queryClient.invalidateQueries({ queryKey: ['pipelines'] });
    },
    onError: (error: any) => {
      toast.error(error?.message || 'Falha ao interromper pipeline.');
    },
  });

  const enrichMutation = useMutation({
    mutationFn: () => fetcher(`/pipelines/${encodeURIComponent(pipe.id)}/enrichment`, {
      method: 'POST',
      body: JSON.stringify({ threshold: 0, max_items: 50 }),
    }),
    onSuccess: () => {
      toast.success('Enriquecimento de candidatos agendado.');
      void queryClient.invalidateQueries({ queryKey: ['pipelines'] });
    },
    onError: (error: any) => {
      toast.error(error?.message || 'Falha ao agendar enriquecimento.');
    },
  });

  const reviewRetryMutation = useMutation({
    mutationFn: () => fetcher(`/pipelines/${encodeURIComponent(pipe.id)}/agent-review/retry`, { method: 'POST' }),
    onSuccess: () => {
      toast.success('Revisões com falha enfileiradas novamente.');
      void queryClient.invalidateQueries({ queryKey: ['pipelines'] });
    },
    onError: (error: any) => toast.error(error?.message || 'Falha ao repetir revisões.'),
  });

  const canResume = workload.mode === 'high_volume' && Number(workload.pending_detail_tasks || 0) > 0 && ['completed_partial', 'waiting_worker', 'waiting_budget', 'failed'].includes(pipe.status);
  const canEnrich = workload.mode === 'high_volume' && Number(workload.discovered || 0) > 0 && terminal && !canResume;

  const progressPercent = useMemo(() => {
    if (!isExecuting) return 100;
    if (workload.mode === 'high_volume') {
      const plannedSecs = Math.max(60, (Number(workload.duration_minutes) || 180) * 60);
      const timePercent = (liveDuration / plannedSecs) * 100;
      const targetDiscovered = Number(workload.discovery_target) || 2000;
      const discPercent = (Number(workload.discovered || 0) / targetDiscovered) * 100;
      return Math.min(98, Math.max(3, Math.round(Math.max(timePercent, discPercent))));
    }
    const targetCount = Number(configuration.limit) || 10;
    const countPercent = (Number(pipe.processed_count || 0) / targetCount) * 100;
    const timePercent = (liveDuration / 20) * 100;
    return Math.min(98, Math.max(3, Math.round(Math.max(countPercent, timePercent))));
  }, [configuration.limit, isExecuting, liveDuration, pipe.processed_count, workload]);

  const statusClass = isRunningSection
    ? 'bg-sky-200 text-black border border-sky-300 font-semibold'
    : pipe.status === 'completed'
      ? 'bg-emerald-100 text-emerald-800 border border-emerald-200'
      : pipe.status === 'failed' || pipe.status === 'cancelled'
        ? 'bg-red-100 text-red-800 border border-red-200'
        : pipe.status === 'blocked'
          ? 'bg-amber-100 text-amber-800 border border-amber-200'
          : isExecuting
            ? 'bg-blue-100 text-blue-800 border border-blue-200'
            : 'bg-slate-100 text-slate-800 border border-slate-200';

  const targetLabel = workload.mode === 'high_volume'
    ? (workload.estimated_discoveries
        ? discoveryEstimateLabel(workload.estimated_discoveries)
        : workload.enrichment_planned
          ? `${workload.enrichment_planned} itens`
          : (workload.discovery_target ? `aprox. ${Number(workload.discovery_target).toLocaleString('pt-BR')}` : 'Alto volume'))
    : configuration.limit
      ? `${configuration.limit} itens`
      : 'Padrão';

  return (
    <article
      className={`relative overflow-hidden rounded-xl transition-all ${
        isRunningSection
          ? 'border-2 border-sky-300 bg-white/95 p-4 text-black shadow-xs'
          : 'border-2 border-slate-300 bg-white p-4 text-slate-900 shadow-xs'
      }`}
    >
      {isExecuting && <ShimmerBar isSky={isRunningSection} />}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className={`text-base font-semibold ${isRunningSection ? 'text-black' : 'text-slate-900'}`}>{label}</h3>
            <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-semibold ${statusClass}`}>
              {isExecuting && <PulseDot dark={isRunningSection} />}
              {translateStatus(pipe.status)}
            </span>
          </div>
          <p className={`mt-0.5 text-xs ${isRunningSection ? 'text-black' : 'text-slate-600'}`}>{formatDateTime(pipe.started_at)}</p>
        </div>

        <div className="flex items-center gap-2">
          {isExecuting && (
            <button
              type="button"
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg border-2 border-red-500 bg-red-50 px-3 py-1.5 text-xs font-bold text-red-700 hover:bg-red-600 hover:text-white transition-all shadow-xs disabled:opacity-50"
            >
              {cancelMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Square size={13} className="fill-current" />}
              {cancelMutation.isPending ? 'Interrompendo...' : 'Parar'}
            </button>
          )}

          {canResume && (
            <button
              type="button"
              onClick={() => resumeMutation.mutate()}
              disabled={resumeMutation.isPending || isExecuting}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {resumeMutation.isPending ? 'Retomando...' : 'Retomar detalhes pendentes'}
            </button>
          )}

          {terminal && Number(workload.agent_failed || 0) > 0 && workload.card_review_mode === 'required' && (
            <button
              type="button"
              onClick={() => reviewRetryMutation.mutate()}
              disabled={reviewRetryMutation.isPending}
              className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-amber-700 disabled:opacity-50"
            >
              {reviewRetryMutation.isPending ? 'Enfileirando...' : 'Repetir revisões com falha'}
            </button>
          )}

          {canEnrich && (
            <button
              type="button"
              onClick={() => enrichMutation.mutate()}
              disabled={enrichMutation.isPending || isExecuting}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
            >
              {enrichMutation.isPending ? 'Agendando...' : 'Enriquecer candidatos'}
            </button>
          )}

          {terminal && (chatAvailable ? (
            <Link
              to={`/pipelines/${encodeURIComponent(pipe.id)}/chat`}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800 transition-colors hover:bg-slate-50 hover:border-slate-400"
            >
              Abrir análise
            </Link>
          ) : (
            <span className="text-xs text-slate-500">{datasetReady ? 'Dataset disponível' : chat.reason_message || 'Análise indisponível'}</span>
          ))}

          <button
            type="button"
            onClick={() => setIsOpen(prev => !prev)}
            aria-label={isOpen ? "Recolher detalhes" : "Expandir detalhes"}
            className={`rounded-lg border p-1.5 transition-colors ${
              isRunningSection
                ? 'border-sky-300 bg-sky-100 text-black hover:bg-sky-200'
                : 'border-slate-300 bg-slate-100 text-slate-800 hover:bg-slate-200'
            }`}
          >
            <ChevronDown size={16} className={`transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </div>

      {isExecuting && (
        <div className="mt-3 w-full" aria-label="Progresso da execução">
          <div className={`h-2 w-full overflow-hidden rounded-full ${isRunningSection ? 'bg-sky-200' : 'bg-slate-200'}`}>
            <div
              className={`h-full rounded-full transition-all duration-700 ease-out ${
                isRunningSection
                  ? 'bg-gradient-to-r from-sky-600 via-sky-400 to-sky-600 animate-pulse'
                  : 'bg-gradient-to-r from-blue-600 via-sky-500 to-blue-600 animate-pulse'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
      )}

      {isOpen && (
        <div className="mt-4 space-y-3">
          <dl
            className={`grid grid-cols-2 gap-2.5 rounded-lg border p-3 sm:grid-cols-4 ${
              isRunningSection
                ? 'border-sky-200 bg-sky-50/80 text-black'
                : 'border-slate-200 bg-slate-50 text-slate-900'
            }`}
          >
            <div>
              <dt className={`text-[11px] font-semibold uppercase tracking-wider ${isRunningSection ? 'text-black' : 'text-slate-500'}`}>
                Quantidade
              </dt>
              <dd className={`mt-0.5 text-base font-bold ${isRunningSection ? 'text-black' : 'text-slate-900'}`}>
                {pipe.processed_count || (workload.discovered ?? 0)}
              </dd>
            </div>
            <div>
              <dt className={`text-[11px] font-semibold uppercase tracking-wider ${isRunningSection ? 'text-black' : 'text-slate-500'}`}>
                Objetivo aprox.
              </dt>
              <dd className={`mt-0.5 text-base font-bold ${isRunningSection ? 'text-black' : 'text-slate-900'}`}>
                {targetLabel}
              </dd>
            </div>
            <div>
              <dt className={`text-[11px] font-semibold uppercase tracking-wider ${isRunningSection ? 'text-black' : 'text-slate-500'}`}>
                Tempo de execução
              </dt>
              <dd className={`mt-0.5 text-base font-bold ${isRunningSection ? 'text-black' : 'text-slate-900'}`}>
                {waitLabel(isExecuting ? liveDuration : pipe.duration_seconds)}
              </dd>
            </div>
            <div>
              <dt className={`text-[11px] font-semibold uppercase tracking-wider ${isRunningSection ? 'text-black' : 'text-slate-500'}`}>
                {isRunningSection ? 'Status em tempo real' : 'Oportunidades'}
              </dt>
              <dd className={`mt-0.5 text-base font-bold ${isRunningSection ? 'text-black' : 'text-slate-900'}`}>
                {isRunningSection ? translateStatus(pipe.status) : (pipe.opportunities_found || 0)}
              </dd>
            </div>
          </dl>

          {workload.mode === 'high_volume' && (
            <div
              className={`rounded-lg border p-3 text-xs leading-relaxed ${
                isRunningSection
                  ? 'border-sky-200 bg-sky-100/70 text-black font-medium'
                  : 'border-slate-200 bg-slate-100 text-slate-800'
              }`}
            >
              <div className="flex flex-wrap gap-x-3 gap-y-1 items-center">
                <span className="font-bold">Alto volume</span>
                {Number(workload.duration_minutes || 0) > 0 && <span>· {workload.duration_minutes} min · {aggressivenessLabel(workload.aggressiveness)}</span>}
                {workload.estimated_discoveries && <span>· Previsto: {discoveryEstimateLabel(workload.estimated_discoveries)}</span>}
                <span>· Descoberta: {workload.discovered || 0}/{workload.discovery_target || 2000}</span>
                {Number(workload.rejected_no_price || 0) > 0 && <span>· Sem preço direto: {workload.rejected_no_price}</span>}
                {Number(workload.rejected_scope || 0) > 0 && <span>· Fora do escopo: {workload.rejected_scope}</span>}
                {Number(workload.rejected_prefilter || 0) > 0 && <span>· Filtrados sem agente: {workload.rejected_prefilter}</span>}
                <span>· Publicados sem auditoria: {workload.cards_published || 0}</span>
                {workload.card_review_mode === 'required' && <span>· Agente: {workload.agent_eligible || 0} elegíveis · {workload.agent_completed || 0} concluídos · {workload.agent_pending || 0} pendentes · {workload.agent_failed || 0} falhas</span>}
                {workload.card_review_mode === 'required' && <span>· Custo estimado: US$ {Number(workload.agent_estimated_cost_usd || 0).toFixed(4)}</span>}
                {Number(workload.cards_withheld || 0) > 0 && <span>· Retidos: {workload.cards_withheld}</span>}
                <span>· Detalhes processados: {workload.enrichment_completed || 0}/{workload.enrichment_planned || 0}</span>
                {Number(workload.pending_detail_tasks || 0) > 0 && (
                  <span>· Pendentes: {workload.pending_detail_tasks}</span>
                )}
                {Number(workload.failed_detail_tasks || 0) > 0 && (
                  <span>· Não verificados: {workload.failed_detail_tasks}</span>
                )}
                <span>· Tentativas: {workload.attempts_count || 1}</span>
                <span>· Navegações: {workload.navigations_consumed || 0}</span>
              </div>
              {workload.phase === 'waiting_budget' && <div className="mt-1 font-semibold text-amber-800">Aguardando disponibilidade de orçamento OLX</div>}
              {workload.phase === 'reviewing_cards' && <div className="mt-1 font-semibold text-blue-800">Revisando cards antes de publicar e acessar anúncios</div>}
              {workload.parser_circuit_open && (
                <div className="mt-1.5 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-amber-900">
                  Circuito pausado: {workload.circuit_trip_reason || 'falhas consecutivas.'}
                </div>
              )}
              {datasetReady && (
                <div className="mt-2 border-t border-slate-200 pt-2 text-xs">
                  <span className="font-semibold">Dados persistidos:</span> {datasetObserved.discoveries || 0} cards · preço utilizável {(datasetPriceCoverage * 100).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%
                  {datasetAnalysis.agent_summary && (
                    <details className="mt-2">
                      <summary className="cursor-pointer font-semibold">Leitura do dataset</summary>
                      <p className="mt-1 whitespace-pre-line leading-5">{text(datasetAnalysis.agent_summary)}</p>
                    </details>
                  )}
                </div>
              )}
            </div>
          )}

          {pipe.scopes?.length > 0 && (
            <details className="text-xs">
              <summary className={`cursor-pointer font-semibold ${isRunningSection ? 'text-black' : 'text-slate-700'}`}>
                Consultas executadas ({pipe.scopes.length})
              </summary>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {pipe.scopes.map((scope: JsonRecord, index: number) => (
                  <div
                    key={scope.id || index}
                    className={`rounded-lg border p-2.5 ${
                      isRunningSection
                        ? 'border-sky-200 bg-sky-50 text-black'
                        : 'border-slate-200 bg-slate-50 text-slate-800'
                    }`}
                  >
                    <div className="flex justify-between gap-2 font-medium">
                      <span className="truncate">{scope.scope?.name || 'Consulta'}</span>
                      <span>{translateStatus(scope.status)}</span>
                    </div>
                    <div className="mt-1 text-xs opacity-80">Resultados: {scope.total_results} · processados: {scope.processed_count}</div>
                    {scope.scope?.catalog_match && <div className="mt-0.5 text-xs opacity-80">Catálogo Brasil: {scope.scope?.catalog_products || 'modelos'} modelos</div>}
                    {scope.error_message && <div className="mt-1 text-xs text-red-600 font-medium">{scope.error_message}</div>}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </article>
  );
}

export default function Pipelines() {
  const { profileId, isManaged } = useProfile();
  const [params, setParams] = useSearchParams();
  const preselectedDefinitionId = params.get('search_definition') || '';
  const [sourceType, setSourceType] = useState<SourceType>('fixture');
  const [runMode, setRunMode] = useState<RunMode>(preselectedDefinitionId ? 'definition' : 'adhoc');
  const [selectedDefinitionId, setSelectedDefinitionId] = useState(preselectedDefinitionId);
  const [selectedScopeIds, setSelectedScopeIds] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState<number | string>(5);
  const [category, setCategory] = useState('gpu');
  const [workloadMode, setWorkloadMode] = useState<WorkloadMode>('standard');
  const [durationMinutes, setDurationMinutes] = useState<number | string>(180);
  const [aggressiveness, setAggressiveness] = useState<Aggressiveness>('balanced');
  const [detailAccessMode, setDetailAccessMode] = useState<'disabled' | 'auto_threshold'>('disabled');
  const [cardReviewMode, setCardReviewMode] = useState<'disabled' | 'required'>('disabled');
  const [useDatasetMatch, setUseDatasetMatch] = useState<boolean>(true);
  const [requirePrice, setRequirePrice] = useState<boolean>(true);
  const [olxPayOnly, setOlxPayOnly] = useState<boolean>(false);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);

  const [isUnifiedOpen, setIsUnifiedOpen] = useState(true);
  const [isRunningOpen, setIsRunningOpen] = useState(true);
  const [isRecentOpen, setIsRecentOpen] = useState(true);

  const queryClient = useQueryClient();
  const enabled = !isManaged || Boolean(profileId);

  const pipelinesQuery = useInfiniteQuery({
    queryKey: ['pipelines', profileId],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => fetcher<any>(`/pipelines?page=${pageParam}&page_size=${PIPELINE_PAGE_SIZE}`),
    getNextPageParam: (lastPage: any, pages) => lastPage.page < lastPage.pages ? pages.length + 1 : undefined,
    enabled,
    refetchInterval: (query) => {
      const data = query.state.data;
      const items = (data as any)?.pages?.flatMap((page: any) => page.items || []) || [];
      const hasActive = items.some((p: any) => isExecutingStatus(p.status));
      if (hasActive) return 1000;
      const hasQueued = items.some((p: any) => ['pending', 'queued'].includes(p.status));
      if (hasQueued) return 2000;
      const hasWaiting = items.some((p: any) => ['waiting_budget', 'waiting_worker'].includes(p.status));
      if (hasWaiting) return 5000;
      return false;
    },
  });

  const definitionsQuery = useQuery<any>({ queryKey: ['search-definitions', profileId], queryFn: () => fetcher('/search-definitions'), enabled });
  const { data: status } = useQuery<any>({ queryKey: ['status'], queryFn: () => fetcher('/status') });
  const { data: marketplaceAuth } = useQuery<any>({ queryKey: ['marketplace-auth', profileId], queryFn: () => fetcher('/marketplace/auth/status'), refetchOnWindowFocus: true, enabled });
  const { data: scopesData } = useQuery<any>({ queryKey: ['search-scopes', profileId, sourceType], queryFn: () => fetcher(`/search-scopes?marketplace=${sourceType}&enabled_only=true`), enabled: sourceType === 'olx' && enabled });
  const rateLimitQuery = useQuery<OlxRateLimit>({
    queryKey: ['marketplace-rate-limit', profileId, 'olx'],
    queryFn: () => fetcher<OlxRateLimit>('/marketplace/rate-limit'),
    enabled: sourceType === 'olx' && enabled,
    staleTime: 5_000,
    refetchInterval: sourceType === 'olx' ? 30_000 : false,
    refetchOnWindowFocus: true,
  });

  const definitions = (definitionsQuery.data?.items || []) as JsonRecord[];
  const selectedDefinition = definitions.find(item => item.id === selectedDefinitionId) || null;
  const pipelines = pipelinesQuery.data?.pages.flatMap((page: any) => page.items || []) || [];

  const activePipelines = pipelines.filter((p: JsonRecord) => isExecutingStatus(p.status));
  const nonActivePipelines = pipelines.filter((p: JsonRecord) => !isExecutingStatus(p.status));
  const recentPipelines = nonActivePipelines.slice(0, RECENT_PIPELINE_COUNT);
  const historicalPipelines = nonActivePipelines.slice(RECENT_PIPELINE_COUNT);

  const isExecuting = activePipelines.length > 0;
  const olxSessionBlocked = marketplaceAuth?.session_state === 'blocked' || ['olx_budget_exhausted', 'olx_circuit_open', 'olx_access_blocked'].includes(marketplaceAuth?.error_code || '');
  const olxSessionReady = Boolean(marketplaceAuth?.authenticated) && !olxSessionBlocked;
  const selectedManualScopes = ((scopesData?.items || []) as JsonRecord[]).filter(scope => selectedScopeIds.includes(text(scope.id)));
  const highVolumeScopeReady = runMode === 'definition'
    ? Boolean(selectedDefinitionId)
    : runMode === 'manual'
      ? selectedScopeIds.length > 0
      : Boolean(query.trim());

  const highVolumePreflightPayload = useMemo(() => {
    const payload: JsonRecord = {
      source_type: sourceType,
      duration_minutes: boundedDuration(durationMinutes),
      aggressiveness,
      detail_access_mode: detailAccessMode,
      card_review_mode: cardReviewMode,
      use_dataset_match: useDatasetMatch,
      require_price: requirePrice,
      olx_pay_only: olxPayOnly,
    };
    if (runMode === 'definition') return { ...payload, search_definition_id: selectedDefinitionId };
    if (runMode === 'manual') return { ...payload, scope_ids: selectedScopeIds };
    if (sourceType === 'olx') {
      return {
        ...payload,
        ad_hoc_scope: { name: 'Busca avulsa', marketplace: 'olx', query, category, limit: boundedLimit(limit, 5), sort: 'recent', enabled: true, require_price: requirePrice, olx_pay_only: olxPayOnly },
      };
    }
    return { ...payload, query, limit: boundedLimit(limit, 5) };
  }, [aggressiveness, cardReviewMode, category, detailAccessMode, durationMinutes, limit, olxPayOnly, query, requirePrice, runMode, selectedDefinitionId, selectedScopeIds, sourceType, useDatasetMatch]);

  const highVolumePreflightQuery = useQuery<HighVolumePreflight>({
    queryKey: ['high-volume-preflight', profileId, JSON.stringify(highVolumePreflightPayload)],
    queryFn: () => fetcher<HighVolumePreflight>('/pipelines/high-volume/preflight', {
      method: 'POST',
      body: JSON.stringify(highVolumePreflightPayload),
    }),
    enabled: enabled && workloadMode === 'high_volume' && highVolumeScopeReady,
    staleTime: 5_000,
  });

  const olxNavigationNeeded = useMemo(() => {
    if (sourceType !== 'olx') return 0;
    if (workloadMode === 'high_volume') {
      if (runMode === 'definition') return selectedDefinition ? 2 : 0;
      if (runMode === 'manual') return selectedManualScopes.length ? 2 : 0;
      return query.trim() ? 2 : 0;
    }
    const numLimit = boundedLimit(limit, 5);
    if (runMode === 'definition') return selectedDefinition ? definitionNavigationEstimate(selectedDefinition, numLimit) : 0;
    if (runMode === 'manual') return selectedManualScopes.length
      ? 1 + selectedManualScopes.reduce((total, scope) => total + 1 + boundedLimit(scope.limit, numLimit), 0)
      : 0;
    return query.trim() ? 2 + boundedLimit(numLimit, 5) : 0;
  }, [limit, query, runMode, selectedDefinition, selectedManualScopes, sourceType, workloadMode]);

  const olxBudgetBlocked = sourceType === 'olx' && workloadMode !== 'high_volume' && Boolean(rateLimitQuery.data) && (
    rateLimitQuery.data!.state === 'cooldown'
    || (olxNavigationNeeded > 0 && rateLimitQuery.data!.remaining < olxNavigationNeeded)
  );

  const olxRateLimitUnavailable = sourceType === 'olx' && workloadMode !== 'high_volume' && rateLimitQuery.isError;
  const highVolumePreflightBlocked = workloadMode === 'high_volume'
    && highVolumeScopeReady
    && Boolean(highVolumePreflightQuery.data)
    && !highVolumePreflightQuery.data!.can_start;

  useEffect(() => {
    if (!preselectedDefinitionId || !definitions.some(item => item.id === preselectedDefinitionId)) return;
    setRunMode('definition');
    setSelectedDefinitionId(preselectedDefinitionId);
  }, [definitions, preselectedDefinitionId]);

  useEffect(() => {
    if (!selectedDefinition) return;
    const plan = selectedDefinition.plan || {};
    const intent = plan.intent || selectedDefinition.intent || {};
    const plannedLimit = Number(plan.desired_count || intent.desired_count);
    if (Number.isFinite(plannedLimit) && plannedLimit > 0) setLimit(Math.min(plannedLimit, 30));
  }, [selectedDefinition]);

  const canRun = useMemo(() => {
    if (workloadMode === 'high_volume' && (
      !highVolumeScopeReady
      || highVolumePreflightQuery.isLoading
      || highVolumePreflightQuery.isError
      || !highVolumePreflightQuery.data?.can_start
      || (cardReviewMode === 'required' && status?.external_providers?.vertex_ai !== 'active')
    )) return false;
    if (sourceType === 'olx' && (
      !olxSessionReady
      || status?.app_mode !== 'live'
      || (workloadMode !== 'high_volume' && (
        rateLimitQuery.isLoading
        || olxRateLimitUnavailable
        || olxBudgetBlocked
      ))
    )) return false;
    if (runMode === 'definition') return Boolean(selectedDefinitionId);
    if (runMode === 'manual') return selectedScopeIds.length > 0;
    return workloadMode === 'high_volume' ? Boolean(query.trim()) : sourceType === 'fixture' || Boolean(query.trim());
  }, [cardReviewMode, highVolumePreflightQuery.data?.can_start, highVolumePreflightQuery.isError, highVolumePreflightQuery.isLoading, highVolumeScopeReady, olxBudgetBlocked, olxRateLimitUnavailable, olxSessionReady, query, rateLimitQuery.isLoading, runMode, selectedDefinitionId, selectedScopeIds.length, sourceType, status?.app_mode, status?.external_providers?.vertex_ai, workloadMode]);

  const mutation = useMutation({
    mutationFn: () => {
      const numLimit = boundedLimit(limit, 5);
      const common = {
        source_type: sourceType,
        fixture_version: 'v1.0.0',
        workload_mode: workloadMode,
        use_dataset_match: useDatasetMatch,
        require_price: requirePrice,
        olx_pay_only: olxPayOnly,
        ...(workloadMode === 'high_volume' ? {
          duration_minutes: boundedDuration(durationMinutes),
          aggressiveness,
          detail_access_mode: detailAccessMode,
          card_review_mode: cardReviewMode,
        } : {}),
      };
      return fetcher('/pipelines/run', {
        method: 'POST',
        body: JSON.stringify(runMode === 'definition'
          ? { ...common, search_definition_id: selectedDefinitionId, limit: numLimit }
          : runMode === 'manual'
            ? { ...common, scope_ids: selectedScopeIds, limit: numLimit }
            : sourceType === 'olx'
              ? { ...common, ad_hoc_scope: { name: 'Busca avulsa', marketplace: 'olx', query, category, limit: numLimit, sort: 'recent', enabled: true, require_price: requirePrice, olx_pay_only: olxPayOnly } }
              : { ...common, query, limit: numLimit }),
      });
    },
    onSuccess: () => {
      setBlockedMessage(null);
      toast.success('Pipeline enfileirado.');
      void queryClient.invalidateQueries({ queryKey: ['pipelines', profileId] });
    },
    onError: (error: any) => {
      const blockedCodes = ['olx_budget_exhausted', 'olx_circuit_open', 'olx_access_blocked'];
      if (sourceType === 'olx' && blockedCodes.includes(error?.code)) {
        const detail = getApiErrorDetail(error);
        const retry = detail?.retry_after_seconds ? ` Disponível em ${waitLabel(detail.retry_after_seconds)}.` : '';
        const message = `${detail?.message || error?.message || 'Pipeline OLX bloqueado.'}${retry}`;
        setBlockedMessage(message);
        toast.warning(message);
        void queryClient.invalidateQueries({ queryKey: ['marketplace-auth', profileId] });
        void queryClient.invalidateQueries({ queryKey: ['marketplace-rate-limit', profileId, 'olx'] });
        return;
      }
      toast.error(error?.message || 'Falha ao enfileirar pipeline.');
    },
  });

  const isRunning = mutation.isPending || isExecuting;

  const chooseDefinition = (definitionId: string) => {
    setRunMode('definition');
    setSelectedDefinitionId(definitionId);
    setParams({ search_definition: definitionId });
  };

  return (
    <div className="space-y-6 p-8 max-w-7xl mx-auto">
      <header>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-slate-900">
          <Play className="text-blue-600" />Pipelines
        </h1>
      </header>

      {/* 1. TOP SECTION: Pipeline Unificado */}
      <section className="relative overflow-hidden rounded-xl border-2 border-slate-300 bg-white p-5 shadow-sm">
        {isRunning && <ShimmerBar />}
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 pb-3.5">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-slate-900">Pipeline unificado</h2>
            {isRunning && <PulseDot />}
          </div>
          <button
            type="button"
            onClick={() => setIsUnifiedOpen(prev => !prev)}
            aria-label={isUnifiedOpen ? "Recolher pipeline unificado" : "Expandir pipeline unificado"}
            className="rounded-lg border border-slate-300 bg-slate-50 p-2 text-slate-700 hover:bg-slate-200 hover:text-black transition-colors"
          >
            <ChevronDown size={18} className={`transition-transform duration-200 ${isUnifiedOpen ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {isUnifiedOpen && (
          <div className="mt-5 space-y-5">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 items-end">
              <label className="flex flex-col gap-1.5 text-xs font-semibold text-slate-700">
                Fonte
                <select
                  value={sourceType}
                  onChange={event => {
                    setSourceType(event.target.value as SourceType);
                    if (event.target.value !== 'olx' && runMode === 'manual') setRunMode('adhoc');
                  }}
                  className="h-10 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                >
                  <option value="fixture">Local</option>
                  <option value="olx" disabled={status?.app_mode !== 'live'}>OLX</option>
                </select>
              </label>

              <label className="flex flex-col gap-1.5 text-xs font-semibold text-slate-700">
                Ritmo
                <select
                  value={workloadMode}
                  onChange={event => {
                    const mode = event.target.value as WorkloadMode;
                    setWorkloadMode(mode);
                  }}
                  className="h-10 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                >
                  <option value="standard">Padrão</option>
                  <option value="high_volume">Alto volume</option>
                </select>
              </label>

              <label className="flex flex-col gap-1.5 text-xs font-semibold text-slate-700">
                Agressividade
                <select
                  aria-label="Agressividade"
                  value={aggressiveness}
                  onChange={event => setAggressiveness(event.target.value as Aggressiveness)}
                  className="h-10 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                >
                  <option value="conservative">Conservador</option>
                  <option value="balanced">Equilibrado</option>
                  <option value="intensive">Intensivo</option>
                </select>
              </label>

              {workloadMode === 'high_volume' ? (
                <label className="flex flex-col gap-1.5 text-xs font-semibold text-slate-700">
                  Duração
                  <input
                    aria-label="Duração em minutos"
                    type="number"
                    min={5}
                    max={180}
                    value={durationMinutes}
                    onChange={event => {
                      const raw = event.target.value;
                      if (raw === '') {
                        setDurationMinutes('');
                        return;
                      }
                      setDurationMinutes(raw.replace(/^0+/, '') || '');
                    }}
                    onBlur={() => setDurationMinutes(boundedDuration(durationMinutes))}
                    className="h-10 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                  />
                </label>
              ) : (
                <label className="flex flex-col gap-1.5 text-xs font-semibold text-slate-700">
                  Quantidade
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={limit}
                    onChange={event => {
                      const raw = event.target.value;
                      if (raw === '') {
                        setLimit('');
                        return;
                      }
                      const cleaned = raw.replace(/^0+/, '');
                      event.currentTarget.value = cleaned;
                      setLimit(cleaned === '' ? '' : Math.min(30, Math.max(1, Number(cleaned))));
                    }}
                    onBlur={event => {
                      const num = Number(limit);
                      if (!limit || isNaN(num) || num < 1) {
                        setLimit(5);
                        event.currentTarget.value = '5';
                      } else if (num > 30) {
                        setLimit(30);
                        event.currentTarget.value = '30';
                      }
                    }}
                    className="h-10 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                  />
                </label>
              )}
            </div>

            {/* Execution Toggles: Dataset, Detail Access, Card Agent, Price Required, OLX Pay */}
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3 pt-1">
              <label className="flex items-center gap-2 text-xs font-semibold text-slate-700 cursor-pointer">
                <input
                  aria-label="Validar com dataset de catálogo"
                  type="checkbox"
                  checked={useDatasetMatch}
                  onChange={event => setUseDatasetMatch(event.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                Validar com dataset
              </label>

              <label className="flex items-center gap-2 text-xs font-semibold text-slate-700 cursor-pointer">
                <input
                  aria-label="Apenas anúncios com preço"
                  type="checkbox"
                  checked={requirePrice}
                  onChange={event => setRequirePrice(event.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                Apenas com preço
              </label>

              <label className="flex items-center gap-2 text-xs font-semibold text-slate-700 cursor-pointer">
                <input
                  aria-label="Garantia OLX / Entrega fácil"
                  type="checkbox"
                  checked={olxPayOnly}
                  onChange={event => setOlxPayOnly(event.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                Garantia OLX / Entrega
              </label>

              {workloadMode === 'high_volume' && (
                <>
                  <label className="flex items-center gap-2 text-xs font-semibold text-slate-700 cursor-pointer">
                    <input
                      aria-label="Acessar anúncios nesta execução"
                      type="checkbox"
                      checked={detailAccessMode === 'auto_threshold'}
                      onChange={event => setDetailAccessMode(event.target.checked ? 'auto_threshold' : 'disabled')}
                      className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                    />
                    Acessar anúncios nesta execução
                  </label>
                  <label className="flex items-center gap-2 text-xs font-semibold text-slate-700 cursor-pointer">
                    <input
                      aria-label="Revisar cards com agente antes de publicar"
                      type="checkbox"
                      checked={cardReviewMode === 'required'}
                      disabled={status?.external_providers?.vertex_ai !== 'active'}
                      onChange={event => setCardReviewMode(event.target.checked ? 'required' : 'disabled')}
                      className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
                    />
                    Revisar cards com agente antes de publicar
                  </label>
                </>
              )}
            </div>

            {workloadMode === 'high_volume' && (
              <div className="flex flex-col gap-1 text-xs font-semibold text-slate-700">
                Previsão
                <div className="rounded-lg border border-slate-300 bg-slate-50 px-3.5 py-2.5 text-sm font-medium text-slate-900">
                  {highVolumePreflightQuery.isLoading || highVolumePreflightQuery.isFetching
                    ? 'Calculando volume...'
                    : highVolumePreflightQuery.data
                      ? highVolumeForecastLabel(highVolumePreflightQuery.data)
                      : 'Selecione um escopo para calcular'}
                </div>
              </div>
            )}
            {workloadMode === 'high_volume' && status?.external_providers?.vertex_ai !== 'active' && (
              <div className="text-xs font-medium text-slate-500">Revisão por agente indisponível nesta configuração.</div>
            )}

            <div className="border-t border-slate-200 pt-4">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                <div className="flex flex-wrap gap-2" role="tablist" aria-label="Modo de escopo">
                  <button
                    type="button"
                    onClick={() => setRunMode('definition')}
                    className={`rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${runMode === 'definition' ? 'bg-slate-900 text-white shadow-xs' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-100'}`}
                  >
                    Escopos salvos
                  </button>
                  <button
                    type="button"
                    onClick={() => { setRunMode('adhoc'); setParams({}); }}
                    className={`rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${runMode === 'adhoc' ? 'bg-slate-900 text-white shadow-xs' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-100'}`}
                  >
                    Busca ampla
                  </button>
                  {sourceType === 'olx' && (
                    <button
                      type="button"
                      onClick={() => setRunMode('manual')}
                      className={`rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${runMode === 'manual' ? 'bg-slate-900 text-white shadow-xs' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-100'}`}
                    >
                      Escopos manuais
                    </button>
                  )}
                </div>
                <Link to="/searches" className="text-sm font-semibold text-blue-600 hover:underline">
                  Abrir buscas
                </Link>
              </div>

              {runMode === 'definition' && (
                <div className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    {definitionsQuery.isLoading ? (
                      <p className="text-sm text-slate-500">Carregando escopos salvos...</p>
                    ) : definitions.map(definition => (
                      <button
                        key={definition.id}
                        type="button"
                        onClick={() => chooseDefinition(definition.id)}
                        className={`rounded-lg border-2 p-3 text-left transition-all ${selectedDefinitionId === definition.id ? 'border-blue-600 bg-blue-50/50 ring-1 ring-blue-600' : 'border-slate-300 bg-white hover:border-slate-400 hover:bg-slate-50'}`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-semibold text-slate-900">{definition.name}</span>
                          {selectedDefinitionId === definition.id && <Check size={16} className="shrink-0 text-blue-600" />}
                        </div>
                        <p className="mt-1 text-xs text-slate-600 truncate">{definitionSummary(definition).join(' · ') || 'Escopo personalizado'}</p>
                      </button>
                    ))}
                    {!definitionsQuery.isLoading && !definitions.length && <p className="text-sm text-slate-500">Nenhum escopo salvo.</p>}
                  </div>

                  {selectedDefinition && (
                    <div className="rounded-lg border border-slate-300 bg-slate-50 p-3">
                      <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <Search size={16} className="text-blue-600" />{selectedDefinition.name}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {definitionSummary(selectedDefinition).map(item => (
                          <span key={item} className="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-xs font-medium text-slate-800">{item}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {runMode === 'adhoc' && (
                <div className="flex flex-wrap gap-3 items-end">
                  <label className="min-w-64 flex-1 text-xs font-semibold text-slate-700">
                    Consulta
                    <input
                      aria-label="Consulta"
                      value={query}
                      onChange={event => setQuery(event.target.value)}
                      placeholder="Termo de busca"
                      className="mt-1 h-10 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                    />
                  </label>
                  {sourceType === 'olx' && (
                    <label className="text-xs font-semibold text-slate-700">
                      Categoria
                      <select
                        value={category}
                        onChange={event => setCategory(event.target.value)}
                        className="mt-1 h-10 block rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                      >
                        <option value="gpu">GPU</option>
                        <option value="notebook">Notebook</option>
                        <option value="cpu">CPU</option>
                        <option value="ram">RAM</option>
                        <option value="ssd">SSD</option>
                        <option value="motherboard">Placa-mãe</option>
                        <option value="desktop">Desktop</option>
                        <option value="other">Outro</option>
                      </select>
                    </label>
                  )}
                </div>
              )}

              {runMode === 'manual' && sourceType === 'olx' && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {(scopesData?.items || []).map((scope: JsonRecord) => (
                    <label key={scope.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-slate-300 bg-white p-3 hover:bg-slate-50">
                      <input
                        type="checkbox"
                        checked={selectedScopeIds.includes(scope.id)}
                        onChange={event => setSelectedScopeIds(current => event.target.checked ? [...current, scope.id] : current.filter(id => id !== scope.id))}
                        className="mt-1"
                      />
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold text-slate-800">{scope.name}</span>
                        <span className="block truncate text-xs text-slate-500">{scope.query} · {scope.category || 'todas as categorias'}</span>
                      </span>
                    </label>
                  ))}
                  {scopesData?.items?.length === 0 && <p className="text-sm text-slate-500">Nenhum escopo manual disponível.</p>}
                </div>
              )}
            </div>

            <div className="border-t border-slate-200 pt-4 flex flex-wrap items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => mutation.mutate()}
                disabled={mutation.isPending || !canRun}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-blue-600 px-6 text-sm font-semibold text-white shadow-xs transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {mutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                {mutation.isPending ? 'Enfileirando...' : 'Executar pipeline'}
              </button>
            </div>

            {sourceType === 'olx' && !olxSessionReady && (
              <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                {olxSessionBlocked ? 'Pipeline OLX bloqueado: orçamento, cooldown ou acesso impedem novas consultas.' : 'Conecte a sessão OLX antes de executar.'}{' '}
                <Link to="/settings" className="font-semibold underline underline-offset-2">Abrir configurações</Link>.
              </div>
            )}
            {sourceType === 'olx' && rateLimitQuery.isLoading && (
              <div className="text-sm text-slate-600">Verificando disponibilidade OLX...</div>
            )}
            {olxRateLimitUnavailable && (
              <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <span>Não foi possível verificar o orçamento OLX antes da execução.</span>
                <button type="button" onClick={() => void rateLimitQuery.refetch()} disabled={rateLimitQuery.isFetching} className="font-semibold underline underline-offset-2 disabled:opacity-50">
                  {rateLimitQuery.isFetching ? 'Atualizando...' : 'Atualizar disponibilidade'}
                </button>
              </div>
            )}
            {olxBudgetBlocked && (
              <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <span className="font-semibold">Orçamento OLX indisponível para este escopo.</span> São necessárias {olxNavigationNeeded} navegações e há {rateLimitQuery.data?.remaining ?? 0} disponíveis. {rateLimitQuery.data?.retry_after_seconds ? `Disponível em ${waitLabel(rateLimitQuery.data.retry_after_seconds)}.` : 'Aguarde a liberação do orçamento.'}
              </div>
            )}
            {workloadMode === 'high_volume' && highVolumePreflightQuery.isError && (
              <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <span>Não foi possível calcular o volume antes da execução.</span>
                <button type="button" onClick={() => void highVolumePreflightQuery.refetch()} className="font-semibold underline underline-offset-2">Atualizar previsão</button>
              </div>
            )}
            {workloadMode === 'high_volume' && highVolumePreflightBlocked && (
              <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <span className="font-semibold">Volume ainda não pode iniciar.</span>{' '}
                {highVolumePreflightQuery.data?.message || 'Aguarde a capacidade OLX.'}
                {highVolumePreflightQuery.data?.retry_after_seconds ? ` Disponível em ${waitLabel(highVolumePreflightQuery.data.retry_after_seconds)}.` : ''}
              </div>
            )}
            {blockedMessage && (
              <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">{blockedMessage}</div>
            )}
          </div>
        )}
      </section>

      {/* 2. MIDDLE SECTION: Pipeline em execução */}
      <section className="relative overflow-hidden rounded-xl border-2 border-sky-300 bg-sky-50 p-5 shadow-sm text-black">
        {isExecuting && <ShimmerBar isSky />}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-black">Pipeline em execução</h2>
            {isExecuting && <PulseDot dark />}
            {activePipelines.length > 0 && (
              <span className="rounded-full bg-sky-200 px-2.5 py-0.5 text-xs font-semibold text-black border border-sky-300">
                {activePipelines.length} ativo{activePipelines.length > 1 ? 's' : ''}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => setIsRunningOpen(prev => !prev)}
            aria-label={isRunningOpen ? "Recolher pipeline em execução" : "Expandir pipeline em execução"}
            className="rounded-lg border border-sky-300 bg-sky-100 p-2 text-black hover:bg-sky-200 transition-colors"
          >
            <ChevronDown size={18} className={`transition-transform duration-200 ${isRunningOpen ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {isRunningOpen && (
          <div className="mt-4 border-t border-sky-200 pt-4">
            {activePipelines.length > 0 ? (
              <div className="space-y-3">
                {activePipelines.map((pipe: JsonRecord) => (
                  <PipelineRunCard key={pipe.id} pipe={pipe} isRunningSection />
                ))}
              </div>
            ) : (
              <div className="py-2 text-sm text-black font-medium">
                Nenhum pipeline em execução no momento.
              </div>
            )}
          </div>
        )}
      </section>

      {/* 3. BOTTOM SECTION: Execuções recentes */}
      <section aria-labelledby="recent-pipelines" className="space-y-3">
        <div className="rounded-xl border-2 border-slate-300 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h2 id="recent-pipelines" className="text-lg font-semibold text-slate-900">Execuções recentes</h2>
              {recentPipelines.length > 0 && (
                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700 border border-slate-200">
                  {recentPipelines.length}
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() => setIsRecentOpen(prev => !prev)}
              aria-label={isRecentOpen ? "Recolher execuções recentes" : "Expandir execuções recentes"}
              className="rounded-lg border border-slate-300 bg-slate-50 p-2 text-slate-700 hover:bg-slate-200 hover:text-black transition-colors"
            >
              <ChevronDown size={18} className={`transition-transform duration-200 ${isRecentOpen ? 'rotate-180' : ''}`} />
            </button>
          </div>

          {isRecentOpen && (
            <div className="mt-4 border-t border-slate-200 pt-4">
              {pipelinesQuery.isLoading ? (
                <div className="py-4 text-sm text-slate-500">Carregando pipelines...</div>
              ) : pipelinesQuery.isError ? (
                <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700">Erro ao carregar pipelines.</div>
              ) : recentPipelines.length ? (
                <div className="space-y-3">
                  {recentPipelines.map((pipe: JsonRecord) => (
                    <PipelineRunCard key={pipe.id} pipe={pipe} />
                  ))}
                </div>
              ) : (
                <div className="py-4 text-center text-sm text-slate-500">Nenhum pipeline recente.</div>
              )}
            </div>
          )}
        </div>
      </section>

      {/* Historical Executions Accordion */}
      {(historicalPipelines.length > 0 || pipelinesQuery.hasNextPage) && (
        <details className="rounded-xl border-2 border-slate-300 bg-white shadow-sm overflow-hidden">
          <summary className="cursor-pointer px-5 py-4 font-semibold text-slate-800 hover:bg-slate-50 transition-colors">
            Histórico de execuções{historicalPipelines.length ? ` (${historicalPipelines.length})` : ''}
          </summary>
          <div className="space-y-3 border-t border-slate-200 p-5 bg-slate-50/50">
            {historicalPipelines.map((pipe: JsonRecord) => (
              <PipelineRunCard key={pipe.id} pipe={pipe} />
            ))}
            {pipelinesQuery.hasNextPage && (
              <button
                type="button"
                onClick={() => pipelinesQuery.fetchNextPage()}
                disabled={pipelinesQuery.isFetchingNextPage}
                className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-slate-400 hover:bg-slate-50 disabled:opacity-50"
              >
                {pipelinesQuery.isFetchingNextPage ? 'Carregando...' : 'Carregar execuções anteriores'}
              </button>
            )}
          </div>
        </details>
      )}
    </div>
  );
}
