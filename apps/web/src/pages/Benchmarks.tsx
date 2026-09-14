import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetcher } from '../api';
import { BarChart } from 'lucide-react';
import { toast } from 'sonner';
import { formatBRL, formatDateTime, formatPercent, translateStatus } from '../lib/format';
import { FieldTip } from '../components/ui/FieldTip';
import { useProfile } from '../profile';

function metricNumber(metrics: any, keys: string[]): number | null {
  for (const key of keys) {
    const value = metrics?.[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
}

function metricText(metrics: any, keys: string[], options: { percent?: boolean; digits?: number } = {}): string {
  const value = metricNumber(metrics, keys);
  if (value === null) return '—';
  if (options.percent) return formatPercent(value);
  if (options.digits !== undefined) return value.toFixed(options.digits);
  return value.toLocaleString('pt-BR', { maximumFractionDigits: options.digits ?? 3 });
}

function comparisonFor(run: any): any {
  return run?.benchmark_comparison || run?.details?.benchmark_comparison || run?.details?.comparison || run?.comparison || null;
}

function compactValue(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value === null || value === undefined) return '—';
  try { return JSON.stringify(value); } catch { return String(value); }
}

function BenchmarkComparison({ run }: { run: any }) {
  const comparison = comparisonFor(run);
  const details = run?.details || {};
  if (!comparison && !run?.compiler_version && !run?.compilerVersion && !details?.compiler_version && !details?.compilerVersion) return null;
  const compilerVersion = comparison?.compiler_version || comparison?.compilerVersion || run?.compiler_version || run?.compilerVersion || details?.compiler_version || details?.compilerVersion;
  const datasetVersion = comparison?.dataset_version || comparison?.datasetVersion || run?.dataset_version || details?.dataset_version;
  const strategyRows = comparison?.strategies || comparison?.strategy_comparison || details?.search_strategies || details?.strategies || [];
  const strategies = Array.isArray(strategyRows)
    ? Object.fromEntries(strategyRows.map((item: any) => [item.strategy, item.metrics]))
    : strategyRows;
  const promoted = comparison?.promoted_strategy || comparison?.promotedStrategy || details?.search_selected_strategy || details?.promoted_strategy || details?.promotedStrategy;
  return <div className="mt-3 rounded-md border border-slate-100 bg-slate-50 p-3 text-xs text-slate-600"><div className="flex flex-wrap gap-x-4 gap-y-1"><span>Versão do compilador: <strong>{compilerVersion ? compactValue(compilerVersion) : '—'}</strong></span><span>Dataset: <strong>{datasetVersion ? compactValue(datasetVersion) : '—'}</strong></span>{promoted && <span>Estratégia promovida: <strong>{compactValue(promoted)}</strong></span>}</div>{strategies && typeof strategies === 'object' && <div className="mt-2 flex flex-wrap gap-2">{(['literal', 'static', 'adaptive'] as const).map(name => strategies[name] !== undefined ? <span key={name} className="rounded bg-white px-2 py-1">{name}: {compactValue(strategies[name])}</span> : null)}</div>}</div>;
}

export default function Benchmarks() {
  const { profileId, isManaged } = useProfile();
  const queryClient = useQueryClient();
  
  const { data, isLoading, error } = useQuery({
    queryKey: ['benchmarks'],
    queryFn: () => fetcher<any>('/benchmarks?page=1&page_size=10'),
    enabled: !isManaged || Boolean(profileId),
  });

  const mutation = useMutation({
    mutationFn: () => fetcher('/benchmarks/run', { method: 'POST', body: JSON.stringify({ dataset_version: 'v1.0.0' }) }),
    onSuccess: () => {
      toast.success('Suíte de benchmark iniciada com sucesso');
      queryClient.invalidateQueries({ queryKey: ['benchmarks'] });
    },
    onError: () => {
      toast.error('Falha ao iniciar benchmark');
    }
  });

  return (
    <div className="p-8 space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-semibold text-slate-900 flex items-center gap-2">
          <BarChart className="text-slate-400" />
          Benchmarks
        </h1>
        <button 
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
          className="bg-accent text-white px-4 py-2 rounded-md font-medium text-sm hover:bg-accent-hover transition-colors disabled:opacity-50 shadow-sm"
        >
          {mutation.isPending ? 'Iniciando...' : 'Executar Benchmark'}
        </button>
      </div>

      {isLoading ? (
        <div className="text-slate-500">Carregando benchmarks...</div>
      ) : error ? (
        <div className="text-red-500">Erro ao carregar benchmarks.</div>
      ) : data?.items?.length > 0 ? (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <MetricCard
              title="Acurácia de Normalização"
              value={metricText(data.items[0].metrics, ['normalization_accuracy'], { percent: true })}
              tip="Taxa de precisão na extração e padronização dos atributos dos anúncios."
            />
            <MetricCard
              title="Precisão de Comparáveis@5"
              value={metricText(data.items[0].metrics, ['comparable_precision_at_5'], { percent: true })}
              tip="Precisão dos top 5 anúncios comparáveis selecionados pelo algoritmo."
            />
            <MetricCard
              title="MRR de Recuperação"
              value={metricText(data.items[0].metrics, ['retrieval_mrr'], { digits: 3 })}
              tip="Mean Reciprocal Rank: eficiência na recuperação e ranqueamento de similares."
            />
            <MetricCard
              title="MAE de Erro de Preço"
              value={metricNumber(data.items[0].metrics, ['price_error_mae']) === null ? '—' : formatBRL(metricNumber(data.items[0].metrics, ['price_error_mae']) as number)}
              tip="Erro Médio Absoluto do algoritmo de precificação em relação ao valor real de mercado."
            />
            <MetricCard title="F1 de Intenção de Busca" value={metricText(data.items[0].metrics, ['search_intent_f1', 'intent_f1'], { percent: true })} tip="F1 da classificação de intenção da busca." />
            <MetricCard title="Violações duras" value={metricText(data.items[0].metrics, ['search_hard_violations', 'hard_violations', 'hard_violation_count'], { digits: 0 })} tip="Quantidade de violações de restrições obrigatórias." />
            <MetricCard title="Precisão@10" value={metricText(data.items[0].metrics, ['search_p_at_10', 'precision_at_10', 'p_at_10'], { percent: true })} tip="Precisão dos dez primeiros resultados." />
            <MetricCard title="Recall@30" value={metricText(data.items[0].metrics, ['search_r_at_30', 'recall_at_30', 'r_at_30'], { percent: true })} tip="Recall dos trinta primeiros resultados." />
            <MetricCard title="nDCG@10" value={metricText(data.items[0].metrics, ['search_ndcg_at_10', 'ndcg_at_10', 'ndcg_10'], { percent: true })} tip="Qualidade do ranking nos dez primeiros resultados." />
          </div>
          
          <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
            <div className="p-4 border-b border-slate-200 flex items-center justify-between">
              <h2 className="font-medium text-slate-900">Execuções Recentes</h2>
              <FieldTip content="Histórico de suítes de benchmark executadas para validação do modelo." size={14} />
            </div>
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">
                    <span className="inline-flex items-center gap-1">
                      Dataset
                      <FieldTip content="Versão do conjunto de dados de teste utilizado." size={12} />
                    </span>
                  </th>
                  <th className="px-4 py-3 font-medium">
                    <span className="inline-flex items-center gap-1">
                      Configuração
                      <FieldTip content="Parâmetros e modelo avaliados nesta rodada." size={12} />
                    </span>
                  </th>
                  <th className="px-4 py-3 font-medium">
                    <span className="inline-flex items-center gap-1">
                      Executado em
                      <FieldTip content="Data e hora em que a suíte foi executada." size={12} />
                    </span>
                  </th>
                  <th className="px-4 py-3 font-medium">
                    <span className="inline-flex items-center gap-1">
                      Status
                      <FieldTip content="Resultado final da execução do benchmark." size={12} />
                    </span>
                  </th>
                  <th className="px-4 py-3 font-medium">
                    <span className="inline-flex items-center gap-1">
                      Falhas
                      <FieldTip content="Total de casos em que o resultado divergiu do esperado." size={12} />
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((run: any) => (
                  <tr key={run.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                    <td className="px-4 py-3 text-slate-900 font-medium">{run.dataset_version}</td>
                    <td className="px-4 py-3 text-slate-600">{run.configuration}<BenchmarkComparison run={run} /></td>
                    <td className="px-4 py-3 text-slate-600">{formatDateTime(run.executed_at)}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${run.status === 'completed' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-800'}`}>
                        {translateStatus(run.status)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-900 font-medium">{run.failures_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.items.some((run: any) => comparisonFor(run) || run.compiler_version || run.compilerVersion || run.details?.compiler_version || run.details?.compilerVersion) && <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-medium text-slate-900">Comparação de estratégias</h2><div className="space-y-2">{data.items.map((run: any) => <BenchmarkComparison key={run.id} run={run} />)}</div></section>}
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-slate-500 shadow-sm">
          Nenhuma execução de benchmark disponível.
        </div>
      )}
    </div>
  );
}

function MetricCard({ title, value, tip }: { title: string; value: string; tip?: string }) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-5 shadow-sm">
      <div className="text-sm text-slate-500 font-medium mb-2 flex items-center gap-1">
        {title}
        {tip && <FieldTip content={tip} size={12} />}
      </div>
      <div className="text-2xl font-semibold text-slate-900">{value}</div>
    </div>
  );
}
