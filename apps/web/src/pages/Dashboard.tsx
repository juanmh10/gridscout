import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetcher } from '../api';
import { Activity, Target, Play } from 'lucide-react';
import { formatBRL, formatPercent, translateStatus, translateCategory } from '../lib/format';
import { CategoryStrip } from '../components/CategoryStrip';
import { useProfile } from '../profile';

export default function Dashboard() {
  const { profileId, isManaged } = useProfile();
  const [selectedCategoryId, setSelectedCategoryId] = useState('all');

  const query = new URLSearchParams();
  if (selectedCategoryId && selectedCategoryId !== 'all') {
    query.set('category', selectedCategoryId);
  }

  const { data, isLoading, error } = useQuery({
    queryKey: ['dashboard', profileId, selectedCategoryId],
    queryFn: () => fetcher<any>(`/dashboard${query.toString() ? `?${query.toString()}` : ''}`),
    enabled: !isManaged || Boolean(profileId),
  });

  if (isLoading) {
    return <div className="p-8 text-slate-500">Carregando painel...</div>;
  }

  if (error) {
    return (
      <div className="p-8 text-red-500">
        Falha ao carregar o painel.{' '}
        <button onClick={() => window.location.reload()} className="underline font-medium">
          Tentar novamente
        </button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="p-8 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 flex items-center gap-2">
            <Activity className="text-slate-400" />
            Painel Geral
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Visão consolidada de liquidez, margens e status do mercado por categoria.
          </p>
        </div>
      </div>

      <CategoryStrip selectedCategoryId={selectedCategoryId} onSelectCategory={setSelectedCategoryId} />
      
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard
          title="Anúncios Ativos"
          value={data.active_listing_count}
          icon={<Activity className="text-slate-400" />}
        />
        <StatCard
          title="Oportunidades"
          value={data.opportunity_count}
          icon={<Target className="text-slate-400" />}
        />
        <StatCard
          title="Execuções de Pipeline"
          value={data.pipeline_runs_count}
          icon={<Play className="text-slate-400" />}
        />
        <StatCard
          title="Último Status"
          value={translateStatus(data.latest_pipeline_status)}
          className="capitalize"
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Top Market Heat */}
        <div className="bg-white border border-slate-200 rounded-lg p-5 shadow-sm">
          <div className="flex items-center gap-1.5 mb-4">
            <h2 className="text-lg font-medium text-slate-900">Mercados Mais Aquecidos</h2>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-slate-500">
                <th className="pb-2 font-medium">Produto / Coorte</th>
                <th className="pb-2 font-medium">Aquecimento</th>
                <th className="pb-2 font-medium">Mediana</th>
              </tr>
            </thead>
            <tbody>
              {data.top_market_heat?.map((item: any) => (
                <tr key={item.product_id} className="border-b border-slate-50 last:border-0">
                  <td className="py-3 pr-2 font-medium text-slate-900">{item.product_name}</td>
                  <td className="py-3 pr-2">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${item.heat_band === 'HOT' ? 'bg-orange-100 text-orange-800' : 'bg-slate-100 text-slate-800'}`}>
                      {item.heat_score.toFixed(1)}
                    </span>
                  </td>
                  <td className="py-3 text-slate-600 font-medium">{formatBRL(item.median_price)}</td>
                </tr>
              ))}
              {(!data.top_market_heat || data.top_market_heat.length === 0) && (
                <tr><td colSpan={3} className="py-4 text-slate-500 text-center">Nenhum mercado aquecido encontrado.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Top Opportunities */}
        <div className="bg-white border border-slate-200 rounded-lg p-5 shadow-sm">
          <div className="flex items-center gap-1.5 mb-4">
            <h2 className="text-lg font-medium text-slate-900">Principais Oportunidades</h2>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-slate-500">
                <th className="pb-2 font-medium">Produto</th>
                <th className="pb-2 font-medium">Pontuação</th>
                <th className="pb-2 font-medium">Margem</th>
              </tr>
            </thead>
            <tbody>
              {data.top_opportunities?.map((opp: any) => (
                <tr key={opp.id} className="border-b border-slate-50 last:border-0">
                  <td className="py-3 pr-2 font-medium text-slate-900">{opp.product_name}</td>
                  <td className="py-3 pr-2 font-semibold text-slate-900">{opp.final_score.toFixed(1)}</td>
                  <td className="py-3 text-emerald-600 font-medium">{formatPercent(opp.price_edge)}</td>
                </tr>
              ))}
              {(!data.top_opportunities || data.top_opportunities.length === 0) && (
                <tr><td colSpan={3} className="py-4 text-slate-500 text-center">Nenhuma oportunidade encontrada.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      
      {/* Market Trend Summary */}
      <div className="bg-white border border-slate-200 rounded-lg p-5 shadow-sm">
        <div className="flex items-center gap-1.5 mb-4">
          <h2 className="text-lg font-medium text-slate-900">Resumo de Tendência de Mercado</h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 text-sm">
          <div>
            <div className="text-slate-500 mb-1">Variação Típica de Preço (7d)</div>
            <div className={`text-lg font-semibold ${data.market_trend_summary?.avg_price_change_7d < 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
              {formatPercent(data.market_trend_summary?.avg_price_change_7d)}
            </div>
          </div>
          <div>
            <div className="text-slate-500 mb-1">Categorias em Alta</div>
            <div className="text-slate-900 font-medium">
              {data.market_trend_summary?.hot_categories?.map(translateCategory).join(', ') || '-'}
            </div>
          </div>
          <div>
            <div className="text-slate-500 mb-1">Categorias em Baixa</div>
            <div className="text-slate-900 font-medium">
              {data.market_trend_summary?.cold_categories?.map(translateCategory).join(', ') || '-'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({
  title,
  value,
  icon,
  className = ""
}: {
  title: string;
  value: string | number;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-5 shadow-sm flex flex-col justify-center">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm text-slate-500 font-medium">{title}</div>
        {icon && <div>{icon}</div>}
      </div>
      <div className={`text-2xl font-semibold text-slate-900 ${className}`}>{value}</div>
    </div>
  );
}
