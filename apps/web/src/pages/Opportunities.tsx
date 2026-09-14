import { Fragment, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetcher } from '../api';
import { CheckCircle2, ChevronDown, ChevronUp, ExternalLink, Image, LayoutGrid, Search, ShieldCheck, Target, Truck, ThumbsDown, ThumbsUp, X, XCircle } from 'lucide-react';
import { formatBRL, formatPercent, formatRecency, formatDateTime, cleanLocation, translateCategory, translateCondition, canonicalSourceUrl } from '../lib/format';
import { CategoryStrip } from '../components/CategoryStrip';
import { useProfile } from '../profile';

type SortField = 'price' | 'score' | 'edge' | 'name' | 'date';
type SortDirection = 'asc' | 'desc';

function deliveryLabel(status: string | undefined) {
  if (status === 'AVAILABLE') return { label: 'Entrega confirmada', className: 'bg-emerald-50 text-emerald-700 border-emerald-200' };
  if (status === 'UNAVAILABLE') return { label: 'Sem entrega', className: 'bg-slate-100 text-slate-600 border-slate-200' };
  return { label: 'Entrega incerta', className: 'bg-amber-50 text-amber-700 border-amber-200' };
}

function sellerLabel(level: string | undefined, verification: string | undefined) {
  if (verification === 'VERIFIED') return 'Selo visível';
  if (level === 'HIGH') return 'Sinais fortes';
  if (level === 'MEDIUM') return 'Sinais médios';
  return 'Sem confirmação';
}

function heatBandLabel(band: string | undefined) {
  if (band === 'HOT') return { label: 'Alta procura', className: 'bg-orange-100 text-orange-800 border-orange-200' };
  if (band === 'NORMAL') return { label: 'Estável', className: 'bg-emerald-50 text-emerald-700 border-emerald-200' };
  if (band === 'COOL' || band === 'COLD') return { label: 'Baixa procura', className: 'bg-slate-100 text-slate-700 border-slate-200' };
  return { label: 'Em análise', className: 'bg-slate-50 text-slate-600 border-slate-200' };
}

function EvidenceList({ items }: { items?: unknown[] }) {
  const values = (items || []).filter(item => typeof item === 'string' && item.trim()) as string[];
  if (!values.length) return <span className="text-slate-400">Nenhuma evidência registrada.</span>;
  return <ul className="space-y-1 text-slate-600">{values.map((item, index) => <li key={`${item}-${index}`}>• {item}</li>)}</ul>;
}

function scoreValue(opportunity: any, key: string): unknown {
  const shortKey = key.replace(/_score$/, '');
  return opportunity?.[key] ?? opportunity?.[shortKey] ?? opportunity?.scores?.[key] ?? opportunity?.scores?.[shortKey] ?? opportunity?.score_breakdown?.[key] ?? opportunity?.score_breakdown?.[shortKey] ?? opportunity?.personalized_scores?.[key] ?? opportunity?.personalized_scores?.[shortKey];
}

function scoreText(value: unknown): string | null {
  if (typeof value !== 'number') return null;
  return value.toFixed(1);
}

function feedbackValue(opportunity: any): string | null {
  const value = opportunity?.profile_feedback ?? opportunity?.feedback ?? opportunity?.feedback_value ?? opportunity?.user_feedback ?? opportunity?.my_feedback;
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') return value.value || value.rating || value.feedback || value.label || value.direction || null;
  return null;
}

function structuredText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return '';
  try { return JSON.stringify(value); } catch { return String(value); }
}

export default function Opportunities() {
  const { profileId, isManaged } = useProfile();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [selectedCategoryId, setSelectedCategoryId] = useState('all');
  const [categoryMenuOpen, setCategoryMenuOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [minPrice, setMinPrice] = useState('');
  const [maxPrice, setMaxPrice] = useState('');
  const [daysFilter, setDaysFilter] = useState('');
  const [deliveryFilter, setDeliveryFilter] = useState('');
  const [stageFilter, setStageFilter] = useState('all');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [feedbackReason, setFeedbackReason] = useState<Record<string, string>>({});

  // 3-state sorting: desc -> asc -> default (null)
  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection | null>(null);

  const handleHeaderSort = (field: SortField) => {
    setPage(1);
    if (sortField !== field) {
      setSortField(field);
      setSortDirection(field === 'name' ? 'asc' : 'desc');
    } else if (sortDirection === (field === 'name' ? 'asc' : 'desc')) {
      setSortDirection(field === 'name' ? 'desc' : 'asc');
    } else {
      setSortField(null);
      setSortDirection(null);
    }
  };

  let effectiveSortBy = 'score_desc';
  if (sortField === 'price') {
    effectiveSortBy = sortDirection === 'desc' ? 'price_desc' : 'price_asc';
  } else if (sortField === 'score') {
    effectiveSortBy = sortDirection === 'desc' ? 'score_desc' : 'score_asc';
  } else if (sortField === 'edge') {
    effectiveSortBy = sortDirection === 'desc' ? 'edge_desc' : 'edge_asc';
  } else if (sortField === 'name') {
    effectiveSortBy = sortDirection === 'desc' ? 'name_desc' : 'name_asc';
  } else if (sortField === 'date') {
    effectiveSortBy = sortDirection === 'asc' ? 'date_asc' : 'date_desc';
  }

  const query = new URLSearchParams({ page: String(page), page_size: '20' });
  if (selectedCategoryId && selectedCategoryId !== 'all') query.set('category', selectedCategoryId);
  if (deliveryFilter) query.set('delivery_status', deliveryFilter);
  query.set('stage', stageFilter);
  if (search.trim()) query.set('search', search.trim());
  if (minPrice.trim()) query.set('min_price', minPrice.trim());
  if (maxPrice.trim()) query.set('max_price', maxPrice.trim());
  if (daysFilter) query.set('days', daysFilter);
  query.set('sort_by', effectiveSortBy);

  const { data, isLoading, error } = useQuery({
    queryKey: ['opportunities', profileId, page, selectedCategoryId, deliveryFilter, stageFilter, search, minPrice, maxPrice, daysFilter, effectiveSortBy],
    queryFn: () => fetcher<any>(`/opportunities?${query.toString()}`),
    enabled: !isManaged || Boolean(profileId),
    placeholderData: keepPreviousData,
  });

  const feedbackMutation = useMutation({
    mutationKey: ['opportunity-feedback', profileId],
    mutationFn: ({ opportunityId, value, reason }: { opportunityId: string; value: string; reason?: string }) => fetcher(`/opportunities/${encodeURIComponent(opportunityId)}/feedback`, {
      method: 'PUT',
      body: JSON.stringify({ value, feedback: value, label: value === 'up' ? 'like' : 'dislike', rating: value === 'up' ? 1 : -1, ...(reason ? { reason, reasons: [reason], reason_codes: [reason] } : {}) }),
    }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['opportunities', profileId] }); },
  });

  const clearFeedbackMutation = useMutation({
    mutationKey: ['opportunity-feedback-remove', profileId],
    mutationFn: (opportunityId: string) => fetcher(`/opportunities/${encodeURIComponent(opportunityId)}/feedback`, { method: 'DELETE' }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['opportunities', profileId] }); },
  });

  const selectCategory = (catId: string) => {
    setSelectedCategoryId(catId);
    setPage(1);
  };

  const hasActiveFilters = Boolean(
    search || minPrice || maxPrice || daysFilter || (selectedCategoryId && selectedCategoryId !== 'all') || deliveryFilter || stageFilter !== 'all' || sortField !== null
  );

  const clearFilters = () => {
    setSearch('');
    setMinPrice('');
    setMaxPrice('');
    setDaysFilter('');
    setSelectedCategoryId('all');
    setDeliveryFilter('');
    setStageFilter('all');
    setSortField(null);
    setSortDirection(null);
    setPage(1);
  };

  return (
    <div className="flex min-h-full flex-col space-y-6 p-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-slate-900">
            <Target className="text-slate-400" />
            Oportunidades de Mercado
          </h1>
          <p className="mt-1 text-xs text-slate-500">
            Detecção determinística de distorções de preço, análise de liquidação e evidências auditadas.
          </p>
        </div>

        {hasActiveFilters && (
          <button
            type="button"
            onClick={clearFilters}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-xs hover:bg-slate-50 hover:text-slate-900 transition-colors"
          >
            <X size={13} />
            <span>Limpar filtros ativos</span>
          </button>
        )}
      </div>

      {/* Menu de categorias colapsado na vertical */}
      <div className="rounded-lg border border-slate-200 bg-white shadow-xs overflow-hidden">
        <button
          type="button"
          onClick={() => setCategoryMenuOpen(!categoryMenuOpen)}
          aria-expanded={categoryMenuOpen}
          className="flex w-full items-center justify-between px-3.5 py-2.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <LayoutGrid size={14} className="text-slate-500" />
            <span className="font-semibold text-slate-900">Categorias:</span>
            <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-800 font-medium">
              {translateCategory(selectedCategoryId)}
            </span>
            {selectedCategoryId !== 'all' && (
              <span
                role="button"
                tabIndex={0}
                className="text-[11px] text-slate-500 hover:text-slate-900 underline cursor-pointer ml-1.5"
                onClick={(e) => {
                  e.stopPropagation();
                  selectCategory('all');
                }}
              >
                Ver todas
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
            <span>{categoryMenuOpen ? 'Recolher menu' : 'Expandir menu vertical'}</span>
            {categoryMenuOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </div>
        </button>

        <div className={`border-t border-slate-200 p-2.5 bg-slate-50/50 ${categoryMenuOpen ? 'block' : 'hidden'}`}>
          <CategoryStrip
            selectedCategoryId={selectedCategoryId}
            onSelectCategory={(id) => {
              selectCategory(id);
              setCategoryMenuOpen(false);
            }}
            orientation="vertical"
          />
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-xs">
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-12 text-xs">
          <div className="lg:col-span-4">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
              <input
                type="text"
                placeholder="Buscar por produto, modelo ou título..."
                value={search}
                onChange={(e) => {
                  setPage(1);
                  setSearch(e.target.value);
                }}
                className="w-full rounded-md border border-slate-200 bg-white pl-8 pr-3 py-1.5 text-xs text-slate-900 placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => {
                    setPage(1);
                    setSearch('');
                  }}
                  className="absolute right-2.5 top-2 text-slate-400 hover:text-slate-600"
                  aria-label="Limpar busca"
                >
                  <X size={13} />
                </button>
              )}
            </div>
          </div>

          <div className="lg:col-span-2 grid grid-cols-2 gap-1.5">
            <input
              type="number"
              min="0"
              step="100"
              placeholder="Min R$"
              value={minPrice}
              onChange={(e) => {
                setPage(1);
                setMinPrice(e.target.value);
              }}
              className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-900 placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <input
              type="number"
              min="0"
              step="100"
              placeholder="Max R$"
              value={maxPrice}
              onChange={(e) => {
                setPage(1);
                setMaxPrice(e.target.value);
              }}
              className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-900 placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

          <div className="lg:col-span-2">
            <label htmlFor="days-filter-select" className="sr-only">
              Filtrar por data
            </label>
            <select
              id="days-filter-select"
              aria-label="Filtrar por data"
              value={daysFilter}
              onChange={(e) => {
                setPage(1);
                setDaysFilter(e.target.value);
              }}
              className="w-full rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            >
              <option value="">Período: Todas as datas</option>
              <option value="1">Últimas 24h (Hoje)</option>
              <option value="3">Últimos 3 dias</option>
              <option value="7">Últimos 7 dias</option>
              <option value="15">Últimos 15 dias</option>
              <option value="30">Últimos 30 dias</option>
            </select>
          </div>

          <div className="lg:col-span-2">
            <label htmlFor="stage-filter-select" className="sr-only">
              Filtrar por estágio
            </label>
            <select
              id="stage-filter-select"
              aria-label="Filtrar por estágio"
              value={stageFilter}
              onChange={(e) => {
                setPage(1);
                setStageFilter(e.target.value);
              }}
              className="w-full rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            >
              <option value="all">Estágio: Todos</option>
              <option value="preliminary">Preliminares</option>
              <option value="confirmed">Confirmadas</option>
            </select>
          </div>

          <div className="lg:col-span-2">
            <label htmlFor="delivery-filter-select" className="sr-only">
              Filtrar por entrega
            </label>
            <select
              id="delivery-filter-select"
              aria-label="Filtrar por entrega"
              value={deliveryFilter}
              onChange={(e) => {
                setPage(1);
                setDeliveryFilter(e.target.value);
              }}
              className="w-full rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            >
              <option value="">Entrega: Todas</option>
              <option value="AVAILABLE">Confirmada</option>
              <option value="UNKNOWN">Incerta</option>
              <option value="UNAVAILABLE">Sem entrega</option>
            </select>
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className="rounded-lg border border-slate-200 bg-white p-12 text-center text-xs text-slate-500 shadow-xs">
          Carregando oportunidades...
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50/50 p-12 text-center text-xs text-rose-700 shadow-xs">
          Falha ao carregar oportunidades.
        </div>
      ) : (
        <div className="rounded-lg border border-slate-200 bg-white shadow-xs overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-xs text-left">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 text-[11px] font-medium">
                <tr>
                  <th
                    scope="col"
                    onClick={() => handleHeaderSort('name')}
                    className="px-4 py-2.5 cursor-pointer select-none hover:bg-slate-100 hover:text-slate-900 transition-colors"
                    title="Ordenar por produto (A-Z -> Z-A -> Padrão)"
                  >
                    <div className="flex items-center gap-1">
                      <span>Produto</span>
                      {sortField === 'name' ? (
                        <span className="font-bold text-slate-900">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                      ) : (
                        <span className="text-slate-300 text-[10px]">↕</span>
                      )}
                    </div>
                  </th>

                  <th
                    scope="col"
                    onClick={() => handleHeaderSort('date')}
                    className="px-3 py-2.5 cursor-pointer select-none hover:bg-slate-100 hover:text-slate-900 transition-colors"
                    title="Ordenar por data (Mais recentes -> Mais antigas -> Padrão)"
                  >
                    <div className="flex items-center gap-1">
                      <span>Data</span>
                      {sortField === 'date' ? (
                        <span className="font-bold text-slate-900">{sortDirection === 'desc' ? '↓' : '↑'}</span>
                      ) : (
                        <span className="text-slate-300 text-[10px]">↕</span>
                      )}
                    </div>
                  </th>

                  <th scope="col" className="px-3 py-2.5">
                    <span>Entrega</span>
                  </th>

                  <th
                    scope="col"
                    onClick={() => handleHeaderSort('score')}
                    className="px-3 py-2.5 cursor-pointer select-none hover:bg-slate-100 hover:text-slate-900 transition-colors"
                    title="Ordenar por pontuação (Maior -> Menor -> Padrão)"
                  >
                    <div className="flex items-center gap-1">
                      <span>Pontuação</span>
                      {sortField === 'score' ? (
                        <span className="font-bold text-slate-900">{sortDirection === 'desc' ? '↓' : '↑'}</span>
                      ) : (
                        <span className="text-slate-300 text-[10px]">↕</span>
                      )}
                    </div>
                  </th>

                  <th
                    scope="col"
                    onClick={() => handleHeaderSort('edge')}
                    className="px-3 py-2.5 cursor-pointer select-none hover:bg-slate-100 hover:text-slate-900 transition-colors"
                    title="Ordenar por margem (Maior -> Menor -> Padrão)"
                  >
                    <div className="flex items-center gap-1">
                      <span>Margem</span>
                      {sortField === 'edge' ? (
                        <span className="font-bold text-slate-900">{sortDirection === 'desc' ? '↓' : '↑'}</span>
                      ) : (
                        <span className="text-slate-300 text-[10px]">↕</span>
                      )}
                    </div>
                  </th>

                  <th scope="col" className="px-3 py-2.5">
                    <span>Aquecimento</span>
                  </th>

                  <th
                    scope="col"
                    onClick={() => handleHeaderSort('price')}
                    className="px-3 py-2.5 cursor-pointer select-none hover:bg-slate-100 hover:text-slate-900 transition-colors"
                    title="Ordenar por preço (Maior preço -> Menor preço -> Padrão)"
                  >
                    <div className="flex items-center gap-1">
                      <span>Preço</span>
                      {sortField === 'price' ? (
                        <span className="font-bold text-slate-900">{sortDirection === 'desc' ? '↓' : '↑'}</span>
                      ) : (
                        <span className="text-slate-300 text-[10px]">↕</span>
                      )}
                    </div>
                  </th>

                  <th scope="col" className="px-3 py-2.5">
                    <span>Vendedor</span>
                  </th>

                  <th scope="col" className="px-3 py-2.5 text-center">
                    <span>Fonte</span>
                  </th>

                  <th scope="col" className="px-4 py-2.5 text-center">
                    <span>Auditoria</span>
                  </th>
                </tr>
              </thead>

              <tbody>
                {!data?.items?.length ? (
                  <tr>
                    <td colSpan={10} className="px-4 py-12 text-center text-xs text-slate-400">
                      Nenhuma oportunidade encontrada para os filtros selecionados.
                    </td>
                  </tr>
                ) : (
                  data.items.map((opp: any) => {
                    const isExpanded = expanded === opp.id;
                    const isAudited = Boolean(opp.full_flow_completed || opp.stage === 'confirmed');
                    const delivery = deliveryLabel(opp.delivery_status);
                    const investigation = opp.investigation || {};
                    const seller = investigation.seller || {};
                    const finalScore = opp.stage === 'preliminary' ? opp.preliminary_score : scoreValue(opp, 'final_score');
                    const currentFeedback = feedbackValue(opp);
                    const heat = heatBandLabel(opp.heat_band);

                    return (
                      <Fragment key={opp.id}>
                        <tr className={`border-b border-slate-100 transition-colors ${isExpanded ? 'bg-slate-50' : 'hover:bg-slate-50/70'}`}>
                          <td className="px-4 py-3">
                            <div className="font-medium text-slate-900">{opp.product_name}</div>
                            <div className="text-xs text-slate-500 max-w-[270px] truncate" title={opp.title}>
                              {opp.title}
                            </div>
                            <div className="text-[11px] text-slate-400 mt-1 flex items-center gap-1">
                              <button
                                type="button"
                                onClick={() => selectCategory(opp.category)}
                                className="text-slate-500 hover:text-slate-900 underline transition-colors"
                                title={`Filtrar pela categoria ${translateCategory(opp.category)}`}
                              >
                                {translateCategory(opp.category)}
                              </button>
                              <span>·</span>
                              <span>{cleanLocation(opp.location)}</span>
                            </div>
                          </td>

                          <td className="px-3 py-3 text-[11px] text-slate-600 whitespace-nowrap">
                            <span className="font-medium text-slate-800" title={formatDateTime(opp.first_seen)}>
                              {formatRecency(opp.first_seen)}
                            </span>
                          </td>

                          <td className="px-3 py-3">
                            <button
                              type="button"
                              onClick={() => {
                                setPage(1);
                                setDeliveryFilter(opp.delivery_status === deliveryFilter ? '' : opp.delivery_status);
                              }}
                              title="Clique para filtrar por esta entrega"
                              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium cursor-pointer hover:opacity-80 transition-opacity ${delivery.className}`}
                            >
                              <Truck size={12} aria-hidden="true" />
                              <span>{delivery.label}</span>
                            </button>
                          </td>

                          <td className="px-3 py-3 font-semibold text-slate-900">
                            <div>{scoreText(finalScore) || '—'}</div>
                            <div className="mt-1 space-y-0.5 text-[10px] font-normal text-slate-500">
                              {(['objective_score', 'request_match_score', 'preference_affinity', 'personalized_score'] as const).map(
                                (key) => {
                                  const score = scoreText(scoreValue(opp, key));
                                  return score ? (
                                    <span key={key} className="mr-2 inline-block" title={key}>
                                      {key.replace('_score', '')}: {score}
                                    </span>
                                  ) : null;
                                }
                              )}
                            </div>
                          </td>

                          <td className="px-3 py-3 text-emerald-700 font-medium">
                            {typeof opp.price_edge === 'number' ? formatPercent(opp.price_edge) : '—'}
                          </td>

                          <td className="px-3 py-3">
                            <span className={`inline-flex items-center px-2 py-0.5 rounded border text-[11px] font-medium ${heat.className}`}>
                              {opp.heat_band || heat.label}
                            </span>
                          </td>

                          <td className="px-3 py-3 text-slate-900 font-medium">
                            <div>{opp.asking_price ? formatBRL(opp.asking_price) : <span className="text-xs text-slate-400">Não informado</span>}</div>
                            {opp.estimated_clearing_value > 0 && (
                              <div
                                className="text-[11px] font-normal text-slate-500 mt-0.5"
                                title="Valor de liquidação de mercado"
                              >
                                Liq.: {formatBRL(opp.estimated_clearing_value)}
                              </div>
                            )}
                          </td>

                          <td className="px-3 py-3 text-[11px] text-slate-600">
                            <span className="inline-flex items-center gap-1">
                              <ShieldCheck size={12} aria-hidden="true" className="text-slate-400" />
                              <span>{sellerLabel(opp.seller_signal_level, opp.seller_verification)}</span>
                            </span>
                          </td>

                          <td className="px-3 py-3 text-center">
                            {opp.source_url ? (
                              <a
                                href={canonicalSourceUrl(opp.source_url)}
                                target="_blank"
                                rel="noopener noreferrer"
                                referrerPolicy="no-referrer"
                                aria-label={`Abrir fonte de ${opp.product_name}`}
                                title="Abrir anúncio original"
                                className="inline-flex rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-900 transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-slate-400"
                              >
                                <ExternalLink size={14} aria-hidden="true" />
                              </a>
                            ) : (
                              <span className="text-slate-300">—</span>
                            )}
                          </td>

                          <td className="px-4 py-3 text-center">
                            <div className="flex items-center justify-center gap-1.5">
                              {isAudited ? (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setPage(1);
                                    setStageFilter(stageFilter === 'confirmed' ? 'all' : 'confirmed');
                                  }}
                                  className="inline-flex items-center gap-1 rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 border border-emerald-200 hover:bg-emerald-100 transition-colors"
                                  title="Auditado: página detalhada confirmada. Clique para filtrar confirmadas."
                                >
                                  <CheckCircle2 size={12} className="text-emerald-600" />
                                  <span>Auditado</span>
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setPage(1);
                                    setStageFilter(stageFilter === 'preliminary' ? 'all' : 'preliminary');
                                  }}
                                  className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700 border border-slate-200 hover:bg-slate-200 transition-colors"
                                  title="Não auditado: card preliminar. Clique para filtrar preliminares."
                                >
                                  <XCircle size={12} className="text-slate-500" />
                                  <span>Não auditado</span>
                                </button>
                              )}

                              {opp.stage !== 'preliminary' && (
                                <>
                                  <button
                                    type="button"
                                    onClick={() => feedbackMutation.mutate({ opportunityId: opp.id, value: 'up', reason: feedbackReason[opp.id] })}
                                    aria-label={`Gostei de ${opp.product_name}`}
                                    aria-pressed={currentFeedback === 'up' || currentFeedback === 'positive'}
                                    className={`rounded p-1 transition-colors ${
                                      currentFeedback === 'up' || currentFeedback === 'positive'
                                        ? 'bg-emerald-100 text-emerald-700'
                                        : 'text-slate-400 hover:bg-emerald-50 hover:text-emerald-700'
                                    }`}
                                  >
                                    <ThumbsUp size={13} aria-hidden="true" />
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => feedbackMutation.mutate({ opportunityId: opp.id, value: 'down', reason: feedbackReason[opp.id] })}
                                    aria-label={`Não gostei de ${opp.product_name}`}
                                    aria-pressed={currentFeedback === 'down' || currentFeedback === 'negative'}
                                    className={`rounded p-1 transition-colors ${
                                      currentFeedback === 'down' || currentFeedback === 'negative'
                                        ? 'bg-rose-100 text-rose-700'
                                        : 'text-slate-400 hover:bg-rose-50 hover:text-rose-700'
                                    }`}
                                  >
                                    <ThumbsDown size={13} aria-hidden="true" />
                                  </button>
                                  {currentFeedback && (
                                    <button
                                      type="button"
                                      onClick={() => clearFeedbackMutation.mutate(opp.id)}
                                      aria-label={`Remover feedback de ${opp.product_name}`}
                                      className="rounded p-1 text-slate-400 hover:bg-slate-100 transition-colors"
                                    >
                                      <X size={13} aria-hidden="true" />
                                    </button>
                                  )}
                                </>
                              )}

                              <button
                                type="button"
                                onClick={() => setExpanded(isExpanded ? null : opp.id)}
                                aria-expanded={isExpanded}
                                aria-label={`${isExpanded ? 'Ocultar' : 'Mostrar'} análise de ${opp.product_name}`}
                                className="inline-flex rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-900 transition-colors"
                              >
                                {isExpanded ? <ChevronUp size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}
                              </button>
                            </div>
                          </td>
                        </tr>

                        {isExpanded && (
                          <tr className="border-b border-slate-200 bg-slate-50/75">
                            <td colSpan={10} className="px-6 py-4">
                              <div className="grid gap-4 lg:grid-cols-3 text-xs">
                                <section className="space-y-2">
                                  <h3 className="font-semibold text-slate-800 uppercase tracking-wide text-[11px]">
                                    Resumo do anúncio
                                  </h3>
                                  <p className="leading-relaxed text-slate-600">
                                    {investigation.listing?.nl_summary || opp.analysis_summary || 'Resumo ainda não disponível.'}
                                  </p>
                                  <div className="text-[11px] text-slate-500 pt-1 space-y-1">
                                    <div>Condição: {translateCondition(opp.condition)}</div>
                                    <div>Status da análise: {opp.investigation_status || (isAudited ? 'Auditado' : 'Preliminar')}</div>
                                    <div className="text-slate-600">
                                      {investigation.opportunity_assessment?.price_assessment ||
                                        (typeof opp.price_edge === 'number'
                                          ? `Margem determinística: ${formatPercent(opp.price_edge)}`
                                          : 'Margem pendente de auditoria de detalhe.')}
                                    </div>
                                    {opp.fast_sale_value > 0 && (
                                      <div>Venda rápida estimada: {formatBRL(opp.fast_sale_value)}</div>
                                    )}
                                  </div>
                                  {opp.personalization_explanation && (
                                    <div className="mt-2 rounded-md border border-slate-200 bg-white p-2.5">
                                      <h4 className="text-[10px] font-semibold uppercase tracking-wider text-slate-700">
                                        Personalização
                                      </h4>
                                      <p className="mt-1 whitespace-pre-wrap text-slate-600 leading-snug">
                                        {structuredText(opp.personalization_explanation)}
                                      </p>
                                    </div>
                                  )}
                                </section>

                                <section className="space-y-2">
                                  <h3 className="font-semibold text-slate-800 uppercase tracking-wide text-[11px]">
                                    Entrega e vendedor
                                  </h3>
                                  <div className="text-slate-700 font-medium">{delivery.label}</div>
                                  <EvidenceList items={investigation.delivery?.evidence || opp.delivery_evidence} />
                                  <div className="text-slate-700 font-medium pt-2">
                                    {sellerLabel(opp.seller_signal_level, opp.seller_verification)}
                                  </div>
                                  <EvidenceList items={seller.evidence || opp.seller_evidence} />
                                </section>

                                <section className="space-y-2">
                                  <h3 className="font-semibold text-slate-800 uppercase tracking-wide text-[11px] flex items-center gap-1.5">
                                    <Image size={13} aria-hidden="true" className="text-slate-400" />
                                    <span>Fotos e próximos passos</span>
                                  </h3>
                                  <p className="text-slate-600 leading-relaxed">
                                    {investigation.photos?.findings?.join(' ') ||
                                      (isAudited ? 'Nenhuma análise visual disponível.' : 'Fotos não carregadas em modo preliminar.')}
                                  </p>
                                  <p className="text-[11px] text-slate-400">
                                    {investigation.photos?.limitations?.join(' ') ||
                                      'A análise visual não substitui checagem presencial ou do número de série.'}
                                  </p>
                                  <ul className="space-y-1 text-slate-600 pt-1">
                                    {(investigation.opportunity_assessment?.suggested_next_checks || []).map(
                                      (item: string, index: number) => (
                                        <li key={`${item}-${index}`}>• {item}</li>
                                      )
                                    )}
                                  </ul>
                                </section>
                              </div>

                              <div className="mt-4 border-t border-slate-200 pt-3 flex flex-wrap items-center justify-between gap-3 text-xs">
                                <div className="text-slate-500">
                                  {isAudited
                                    ? 'Evidências da página completa do anúncio.'
                                    : 'Evidências preliminares obtidas do card de busca da OLX.'}
                                </div>
                                <div className="flex items-center gap-4">
                                  <label className="flex items-center gap-1.5 text-slate-500">
                                    <span>Motivo opcional:</span>
                                    <select
                                      aria-label={`Motivo do feedback de ${opp.product_name}`}
                                      value={feedbackReason[opp.id] || ''}
                                      onChange={(event) =>
                                        setFeedbackReason((current) => ({ ...current, [opp.id]: event.target.value }))
                                      }
                                      className="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700"
                                    >
                                      <option value="">Não informado</option>
                                      <option value="price">Preço</option>
                                      <option value="condition">Condição</option>
                                      <option value="location">Localização</option>
                                      <option value="trust">Vendedor</option>
                                      <option value="other">Outro</option>
                                    </select>
                                  </label>
                                  {opp.source_url && (
                                    <a
                                      href={canonicalSourceUrl(opp.source_url)}
                                      target="_blank"
                                      rel="noreferrer noopener"
                                      referrerPolicy="no-referrer"
                                      className="inline-flex items-center gap-1 rounded bg-slate-900 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-800 transition-colors"
                                    >
                                      <ExternalLink size={12} />
                                      <span>Abrir anúncio original para contato</span>
                                    </a>
                                  )}
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <div className="p-3 border-t border-slate-200 flex items-center justify-between text-xs text-slate-500 bg-slate-50/50">
            <div>
              Total: <strong>{data?.total || 0}</strong>
            </div>
            <div className="flex gap-2">
              <button
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
                className="px-3 py-1 border border-slate-200 rounded-md bg-white hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium text-slate-700"
              >
                Anterior
              </button>
              <button
                disabled={page >= (data?.pages || 1)}
                onClick={() => setPage((p) => p + 1)}
                className="px-3 py-1 border border-slate-200 rounded-md bg-white hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium text-slate-700"
              >
                Próximo
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
