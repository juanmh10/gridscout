import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BrainCircuit, Clock3, Coins, Cpu, Gauge, Hash, RefreshCw } from 'lucide-react';
import { fetcher } from '../api';
import { formatDateTime, formatNumber, formatPercent } from '../lib/format';
import { useProfile } from '../profile';

type MetricsWindow = '24h' | '7d' | '30d' | 'all';

interface MetricSummary {
  window: MetricsWindow;
  from?: string | null;
  to?: string | null;
  currency: 'USD' | string;
  estimated_cost_usd?: number | null;
  calls: number;
  success_rate: number;
  fallback_rate: number;
  latency_ms: { average: number; p50: number; p95: number };
  tokens: { prompt: number; candidates: number; thoughts: number; cached: number; tool: number; total: number };
  by_model: Array<{ model_id: string; calls: number; estimated_cost_usd?: number | null; average_latency_ms: number; success_rate: number; tokens_total: number }>;
  by_operation: Array<{ operation: string; calls: number; estimated_cost_usd?: number | null; average_latency_ms: number }>;
}

interface MetricCall {
  id: string;
  pipeline_run_id?: string | null;
  origin: string;
  operation: string;
  provider: string;
  auth_mode: string;
  model_id: string;
  status: string;
  started_at: string;
  duration_ms: number;
  prompt_tokens: number;
  candidates_tokens: number;
  thoughts_tokens: number;
  cached_tokens: number;
  tool_tokens: number;
  total_tokens: number;
  finish_reason?: string | null;
  estimated_cost_usd?: number | null;
  pricing_version?: string | null;
  error_code?: string | null;
}

interface MetricCallsResponse {
  items: MetricCall[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

function costUsd(value: number | null | undefined) {
  // The summary endpoint currently normalizes an unknown nullable cost to 0;
  // zero therefore remains an explicit "unknown" presentation in the UI.
  if (value === null || value === undefined || value === 0) return 'Preço não configurado';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 4, maximumFractionDigits: 6 }).format(value);
}

function metricsParams(window: MetricsWindow, modelId: string, operation: string) {
  const params = new URLSearchParams({ window });
  if (modelId) params.set('model_id', modelId);
  if (operation) params.set('operation', operation);
  return params.toString();
}

function callsParams(window: MetricsWindow, modelId: string, operation: string, status: string) {
  const params = new URLSearchParams({ page: '1', page_size: '50', window });
  if (modelId) params.set('model_id', modelId);
  if (operation) params.set('operation', operation);
  if (status) params.set('status', status);
  return params.toString();
}

export default function AIMetrics() {
  const { profileId, isManaged } = useProfile();
  const [window, setWindow] = useState<MetricsWindow>('24h');
  const [modelId, setModelId] = useState('');
  const [operation, setOperation] = useState('');
  const [status, setStatus] = useState('');

  const { data: summary, isLoading: summaryLoading, error: summaryError, refetch: refetchSummary } = useQuery<MetricSummary>({
    queryKey: ['model-metrics-summary', profileId, window, modelId, operation],
    queryFn: () => fetcher<MetricSummary>(`/model-metrics/summary?${metricsParams(window, modelId, operation)}`),
    enabled: !isManaged || Boolean(profileId),
  });

  const { data: calls, isLoading: callsLoading, error: callsError } = useQuery<MetricCallsResponse>({
    queryKey: ['model-metrics-calls', profileId, window, modelId, operation, status],
    queryFn: () => fetcher<MetricCallsResponse>(`/model-metrics/calls?${callsParams(window, modelId, operation, status)}`),
    enabled: !isManaged || Boolean(profileId),
  });

  const modelOptions = summary?.by_model || [];
  const operationOptions = summary?.by_operation || [];

  return (
    <div className="space-y-6 p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-slate-900">
            <BrainCircuit className="text-slate-400" />
            Métricas de IA
          </h1>
          <p className="mt-1 text-sm text-slate-500">Custos e desempenho das chamadas de modelo no período selecionado.</p>
        </div>
        <button
          type="button"
          onClick={() => void refetchSummary()}
          className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          <RefreshCw size={15} /> Atualizar
        </button>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
          Janela
          <select value={window} onChange={(event) => setWindow(event.target.value as MetricsWindow)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900">
            <option value="24h">Últimas 24h</option>
            <option value="7d">Últimos 7 dias</option>
            <option value="30d">Últimos 30 dias</option>
            <option value="all">Todo o período</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
          Modelo
          <select aria-label="Filtrar por modelo" value={modelId} onChange={(event) => setModelId(event.target.value)} className="min-w-44 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900">
            <option value="">Todos os modelos</option>
            {modelOptions.map((item) => <option key={item.model_id} value={item.model_id}>{item.model_id}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
          Operação
          <select aria-label="Filtrar por operação" value={operation} onChange={(event) => setOperation(event.target.value)} className="min-w-44 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900">
            <option value="">Todas as operações</option>
            {operationOptions.map((item) => <option key={item.operation} value={item.operation}>{item.operation}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
          Status das chamadas
          <select aria-label="Filtrar por status" value={status} onChange={(event) => setStatus(event.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-normal text-slate-900">
            <option value="">Todos</option>
            <option value="success">Sucesso</option>
            <option value="fallback">Fallback</option>
            <option value="error">Erro</option>
          </select>
        </label>
      </div>

      {summaryLoading ? <LoadingMessage text="Carregando resumo de métricas..." /> : summaryError ? <ErrorMessage text="Não foi possível carregar o resumo de métricas." /> : summary && (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard icon={<Coins size={18} />} title="Custo estimado" value={costUsd(summary.estimated_cost_usd)} detail="Estimativa paid standard tier USD" />
            <MetricCard icon={<Hash size={18} />} title="Chamadas" value={formatNumber(summary.calls, 0)} detail={`${formatPercent(summary.success_rate)} sucesso · ${formatPercent(summary.fallback_rate)} fallback`} />
            <MetricCard icon={<Gauge size={18} />} title="Latência média" value={`${formatNumber(summary.latency_ms.average)} ms`} detail={`P50 ${formatNumber(summary.latency_ms.p50)} ms · P95 ${formatNumber(summary.latency_ms.p95)} ms`} />
            <MetricCard icon={<Cpu size={18} />} title="Tokens totais" value={formatNumber(summary.tokens.total, 0)} detail={`Prompt ${formatNumber(summary.tokens.prompt, 0)} · candidatos ${formatNumber(summary.tokens.candidates, 0)}`} />
          </div>

          <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center gap-2">
              <h2 className="text-lg font-medium text-slate-900">Tokens por tipo</h2>
              <span className="text-xs text-slate-500">{summary.window}</span>
            </div>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
              <TokenStat label="Prompt" value={summary.tokens.prompt} />
              <TokenStat label="Candidatos" value={summary.tokens.candidates} />
              <TokenStat label="Pensamentos" value={summary.tokens.thoughts} />
              <TokenStat label="Cache" value={summary.tokens.cached} />
              <TokenStat label="Ferramentas" value={summary.tokens.tool} />
              <TokenStat label="Total" value={summary.tokens.total} emphasized />
            </div>
          </section>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <MetricTable title="Por modelo" headers={['Modelo', 'Chamadas', 'Custo', 'Latência média', 'Sucesso', 'Tokens']}>
              {summary.by_model.map((item) => (
                <tr key={item.model_id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3 font-medium text-slate-900">{item.model_id}</td>
                  <td className="px-4 py-3 text-slate-600">{formatNumber(item.calls, 0)}</td>
                  <td className="px-4 py-3 text-slate-600">{costUsd(item.estimated_cost_usd)}</td>
                  <td className="px-4 py-3 text-slate-600">{formatNumber(item.average_latency_ms)} ms</td>
                  <td className="px-4 py-3 text-slate-600">{formatPercent(item.success_rate)}</td>
                  <td className="px-4 py-3 text-slate-600">{formatNumber(item.tokens_total, 0)}</td>
                </tr>
              ))}
            </MetricTable>
            <MetricTable title="Por operação" headers={['Operação', 'Chamadas', 'Custo', 'Latência média']}>
              {summary.by_operation.map((item) => (
                <tr key={item.operation} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3 font-medium text-slate-900">{item.operation}</td>
                  <td className="px-4 py-3 text-slate-600">{formatNumber(item.calls, 0)}</td>
                  <td className="px-4 py-3 text-slate-600">{costUsd(item.estimated_cost_usd)}</td>
                  <td className="px-4 py-3 text-slate-600">{formatNumber(item.average_latency_ms)} ms</td>
                </tr>
              ))}
            </MetricTable>
          </div>
        </>
      )}

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-200 p-5">
          <div className="flex items-center gap-2"><Clock3 size={18} className="text-slate-500" /><h2 className="text-lg font-medium text-slate-900">Chamadas recentes</h2></div>
          <span className="text-xs text-slate-500">{calls?.total ?? 0} no filtro</span>
        </div>
        {callsLoading ? <LoadingMessage text="Carregando chamadas..." /> : callsError ? <ErrorMessage text="Não foi possível carregar as chamadas recentes." /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500"><tr>{['Início', 'Operação', 'Modelo', 'Status', 'Duração', 'Tokens', 'Custo estimado'].map((header) => <th key={header} className="px-4 py-3 font-medium">{header}</th>)}</tr></thead>
              <tbody>
                {calls?.items.length ? calls.items.map((call) => <CallRow key={call.id} call={call} />) : <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">Nenhuma chamada encontrada.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function MetricCard({ icon, title, value, detail }: { icon: React.ReactNode; title: string; value: string; detail: string }) {
  return <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"><div className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-500">{icon}{title}</div><div className="break-words text-2xl font-semibold text-slate-900">{value}</div><div className="mt-2 text-xs text-slate-500">{detail}</div></div>;
}

function TokenStat({ label, value, emphasized = false }: { label: string; value: number; emphasized?: boolean }) {
  return <div><div className="text-xs text-slate-500">{label}</div><div className={emphasized ? 'mt-1 text-lg font-semibold text-accent' : 'mt-1 text-lg font-medium text-slate-900'}>{formatNumber(value, 0)}</div></div>;
}

function MetricTable({ title, headers, children }: { title: string; headers: string[]; children: React.ReactNode }) {
  return <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"><h2 className="border-b border-slate-200 p-5 text-lg font-medium text-slate-900">{title}</h2><div className="overflow-x-auto"><table className="w-full min-w-[560px] text-left text-sm"><thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500"><tr>{headers.map((header) => <th key={header} className="px-4 py-3 font-medium">{header}</th>)}</tr></thead><tbody>{children}</tbody></table></div></section>;
}

function CallRow({ call }: { call: MetricCall }) {
  const statusClass = call.status === 'success'
    ? 'bg-emerald-50 text-emerald-700'
    : call.status === 'fallback'
      ? 'bg-sky-50 text-sky-700'
      : 'bg-rose-50 text-rose-700';
  return <tr className="border-b border-slate-100 last:border-0 hover:bg-slate-50"><td className="px-4 py-3 text-slate-600">{formatDateTime(call.started_at)}</td><td className="px-4 py-3 font-medium text-slate-900">{call.operation}</td><td className="px-4 py-3 text-slate-600">{call.model_id}</td><td className="px-4 py-3"><span className={`rounded px-2 py-1 text-xs font-medium ${statusClass}`}>{call.status}</span></td><td className="px-4 py-3 text-slate-600">{formatNumber(call.duration_ms)} ms</td><td className="px-4 py-3 text-slate-600">{formatNumber(call.total_tokens, 0)}</td><td className="px-4 py-3 text-slate-600">{costUsd(call.estimated_cost_usd)}</td></tr>;
}

function LoadingMessage({ text }: { text: string }) {
  return <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">{text}</div>;
}

function ErrorMessage({ text }: { text: string }) {
  return <div className="rounded-lg border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">{text}</div>;
}
