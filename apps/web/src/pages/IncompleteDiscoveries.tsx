import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, CircleAlert, Database, ExternalLink, Loader2, Play, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { fetcher } from '../api';
import { formatBRL, formatDateTime, canonicalSourceUrl } from '../lib/format';
import { useProfile } from '../profile';

type AnalysisFilter = 'all' | 'not_requested' | 'pending' | 'running' | 'completed' | 'fallback' | 'failed';
type RecordValue = Record<string, any>;

const labels: Record<AnalysisFilter, string> = {
  all: 'Todos',
  not_requested: 'Não processados',
  pending: 'Na fila',
  running: 'Em análise',
  completed: 'Analisados',
  fallback: 'Fallback local',
  failed: 'Com falha',
};

function text(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value);
}

function analysisLabel(status: string): string {
  return labels[status as AnalysisFilter] || status || 'Não processado';
}

function analysisClass(status: string): string {
  if (status === 'completed') return 'bg-emerald-100 text-emerald-800';
  if (status === 'fallback') return 'bg-amber-100 text-amber-800';
  if (status === 'failed') return 'bg-red-100 text-red-800';
  if (status === 'pending' || status === 'running') return 'bg-blue-100 text-blue-800';
  return 'bg-slate-100 text-slate-700';
}

export default function IncompleteDiscoveries() {
  const { profileId, isManaged } = useProfile();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<AnalysisFilter>('all');
  const [page, setPage] = useState(1);
  const enabled = !isManaged || Boolean(profileId);
  const discoveriesQuery = useQuery<any>({
    queryKey: ['incomplete-discoveries', profileId, filter, page],
    queryFn: () => fetcher(`/incomplete-discoveries?page=${page}&page_size=24&status_filter=${filter}`),
    enabled,
    refetchInterval: query => {
      const summary = (query.state.data as any)?.summary || {};
      return Number(summary.pending || 0) + Number(summary.running || 0) > 0 ? 2_500 : false;
    },
  });
  const queueMutation = useMutation({
    mutationFn: () => fetcher<any>('/incomplete-discoveries/analyze', { method: 'POST' }),
    onSuccess: result => {
      toast.success(result.queued ? `${result.queued} cards enfileirados para análise.` : 'Nenhum card novo precisava ser enfileirado.');
      queryClient.invalidateQueries({ queryKey: ['incomplete-discoveries', profileId] });
    },
    onError: () => toast.error('Não foi possível enfileirar a análise dos cards.'),
  });

  const data = discoveriesQuery.data || { items: [], summary: {}, pages: 1 };
  const summary = (data.summary || {}) as RecordValue;
  const active = Number(summary.pending || 0) + Number(summary.running || 0);
  const processed = Number(summary.completed || 0) + Number(summary.fallback || 0);
  const total = Number(summary.total || 0);

  const selectFilter = (next: AnalysisFilter) => {
    setFilter(next);
    setPage(1);
  };

  return (
    <div className="p-8 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-slate-900"><Database className="text-slate-400" />Dados incompletos</h1>
          <p className="mt-1 text-sm text-slate-600">Cards de busca preservados sem página de detalhe confirmada.</p>
        </div>
        <button
          type="button"
          onClick={() => queueMutation.mutate()}
          disabled={queueMutation.isPending || total === 0}
          className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {queueMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
          Processar com agentes
        </button>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Cards incompletos" value={total} />
        <Metric label="Processados" value={processed} />
        <Metric label="Em análise" value={active} accent={active > 0} />
        <Metric label="Falhas" value={Number(summary.failed || 0)} alert={Number(summary.failed || 0) > 0} />
      </section>

      <div className="flex flex-wrap gap-2">
        {(Object.keys(labels) as AnalysisFilter[]).map(value => (
          <button
            key={value}
            type="button"
            onClick={() => selectFilter(value)}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${filter === value ? 'bg-slate-900 text-white' : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}`}
          >
            {labels[value]}
          </button>
        ))}
      </div>

      {discoveriesQuery.isLoading ? (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">Carregando cards preservados…</div>
      ) : discoveriesQuery.isError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">Não foi possível carregar os dados incompletos.</div>
      ) : data.items.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">Nenhum card nesta seleção.</div>
      ) : (
        <section className="grid gap-3 lg:grid-cols-2">
          {data.items.map((item: RecordValue, index: number) => {
            const analysis = (item.analysis || {}) as RecordValue;
            const confidence = Number(analysis.confidence);
            return (
              <article key={`${item.source_url || item.title}-${index}`} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate font-medium text-slate-900">{item.title || 'Título não informado'}</h2>
                    <p className="mt-1 text-sm text-slate-600">{item.price ? formatBRL(item.price) : 'Preço não informado'}{item.location ? ` · ${item.location}` : ''}</p>
                  </div>
                  <div className="flex items-center gap-2"><span aria-label={item.full_flow_completed ? 'Fluxo completo' : 'Fluxo incompleto'}>{item.full_flow_completed ? <CheckCircle2 size={18} className="text-emerald-600" /> : <XCircle size={18} className="text-red-600" />}</span><span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${analysisClass(text(analysis.status))}`}>{analysisLabel(text(analysis.status))}</span></div>
                </div>

                {(analysis.category || analysis.brand || analysis.model_name) && (
                  <div className="mt-3 flex flex-wrap gap-2 text-sm">
                    {[analysis.category, analysis.brand, analysis.model_name, analysis.variant].filter(Boolean).map((value: unknown) => (
                      <span key={text(value)} className="rounded bg-slate-100 px-2 py-1 text-slate-700">{text(value)}</span>
                    ))}
                    {Number.isFinite(confidence) && <span className="rounded bg-blue-50 px-2 py-1 text-blue-800">confiança {Math.round(confidence * 100)}%</span>}
                    {Number.isFinite(Number(item.preliminary_score)) && <span className="rounded bg-slate-100 px-2 py-1 text-slate-700">score {Number(item.preliminary_score).toFixed(1)}</span>}
                  </div>
                )}

                {analysis.summary && <p className="mt-3 text-sm leading-5 text-slate-700">{text(analysis.summary)}</p>}
                {Array.isArray(analysis.attributes) && analysis.attributes.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-600">
                    {analysis.attributes.map((attribute: RecordValue) => <span key={text(attribute.name)} className="rounded border border-slate-200 px-2 py-1">{text(attribute.name)}: {text(attribute.value)}</span>)}
                  </div>
                )}

                <div className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
                  <div className="flex items-center gap-1 font-medium"><CircleAlert size={14} />Evidência parcial</div>
                  <p className="mt-1">{item.evidence_scope}</p>
                  {Array.isArray(item.missing_evidence) && item.missing_evidence.length > 0 && <p className="mt-1">Não verificado: {item.missing_evidence.join(', ')}.</p>}
                </div>

                <div className="mt-3 flex items-center justify-between gap-2 text-xs text-slate-500">
                  <span>{formatDateTime(item.observed_at)}</span>
                  {item.source_url && <a href={canonicalSourceUrl(item.source_url)} target="_blank" rel="noreferrer noopener" referrerPolicy="no-referrer" className="inline-flex items-center gap-1 font-medium text-slate-700 hover:text-slate-950">Abrir origem <ExternalLink size={13} /></a>}
                </div>
              </article>
            );
          })}
        </section>
      )}

      <div className="flex items-center justify-between border-t border-slate-200 pt-4 text-sm text-slate-600">
        <span>{total} cards preservados</span>
        <div className="flex gap-2">
          <button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)} className="rounded border border-slate-200 px-3 py-1.5 disabled:opacity-50">Anterior</button>
          <button type="button" disabled={page >= Number(data.pages || 1)} onClick={() => setPage(value => value + 1)} className="rounded border border-slate-200 px-3 py-1.5 disabled:opacity-50">Próximo</button>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, accent = false, alert = false }: { label: string; value: number; accent?: boolean; alert?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-sm text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${alert ? 'text-red-700' : accent ? 'text-blue-700' : 'text-slate-900'}`}>{value.toLocaleString('pt-BR')}</div>
    </div>
  );
}
