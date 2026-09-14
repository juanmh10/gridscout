import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, ChevronDown, ChevronUp, ExternalLink, Layers, CheckCircle2, AlertCircle, XCircle, Tag, X } from 'lucide-react';
import { fetcher } from '../api';
import { formatBRL, formatPercent, translateCategory, translateCondition, translateStatus, canonicalSourceUrl } from '../lib/format';
import { CategoryStrip } from '../components/CategoryStrip';
import { FacetFilters } from '../components/FacetFilters';
import { useProfile } from '../profile';

interface Product {
  id: string;
  display_name: string;
  category: string;
  brand?: string;
  family?: string;
  model?: string;
  variant?: string;
  market_cohort_id?: string;
  active_listings_count?: number;
  market_median_price?: number;
  source_url?: string;
  attributes?: Record<string, any>;
}

interface ProductResponse {
  items: Product[];
  total?: number;
}

interface MarketData {
  product_id?: string;
  product_name: string;
  category?: string;
  market_cohort_id?: string;
  market_cohort_name?: string;
  taxonomy_version?: string;
  market_eligible?: boolean;
  market_exclusion_reason?: string | null;
  is_legacy?: boolean;
  heat_band?: string;
  market_heat?: number;
  sample_size?: number;
  active_count?: number;
  included_count?: number;
  unverified_count?: number;
  excluded_count?: number;
  exclusion_summary?: Record<string, number>;
  confidence?: number;
  asking_median?: number;
  estimated_clearing_value?: number;
  fast_sale_value?: number;
  p10?: number;
  p25?: number;
  median?: number;
  p75?: number;
  p90?: number;
  mad?: number;
  preliminary_market?: {
    sample_size?: number;
    included_count?: number;
    asking_median?: number;
    p10?: number;
    p25?: number;
    p75?: number;
    p90?: number;
    confidence?: number;
  } | null;
}

interface ListingItem {
  id: string;
  title: string;
  price: number | null;
  source: string;
  source_url: string;
  location_city: string;
  location_state: string;
  condition: string | null;
  audit_status?: string;
  status: string;
  classification_status?: string;
  analytics_eligible?: boolean;
  first_seen?: string;
  last_seen?: string;
}

interface ListingResponse {
  items: ListingItem[];
  total: number;
}

const QUICK_TAGS = [
  { id: 'ips', label: 'Tela IPS' },
  { id: '16gb', label: '16 GB+ RAM' },
  { id: '32gb', label: '32 GB+ RAM' },
  { id: '512gb', label: '512 GB+ SSD' },
  { id: '1tb', label: '1 TB+ SSD' },
  { id: 'gamer', label: 'Gamer / RTX' },
  { id: 'macbook', label: 'Apple / MacBook' },
  { id: 'ryzen', label: 'AMD Ryzen' },
  { id: 'intel', label: 'Intel Core' },
  { id: 'lacrado', label: 'Novo / Lacrado' },
  { id: 'touch', label: 'Touchscreen' },
];

function productsUrl(params: {
  category: string;
  search?: string;
  minPrice?: string | number;
  maxPrice?: string | number;
  tags?: string[];
  facets?: Record<string, string>;
  sortBy?: string;
}) {
  const q = new URLSearchParams();
  if (params.category && params.category !== 'all') q.set('category', params.category);
  if (params.search && params.search.trim()) q.set('search', params.search.trim());
  if (params.minPrice !== undefined && params.minPrice !== '') q.set('min_price', String(params.minPrice));
  if (params.maxPrice !== undefined && params.maxPrice !== '') q.set('max_price', String(params.maxPrice));
  if (params.tags && params.tags.length > 0) q.set('tags', params.tags.join(','));
  if (params.sortBy) q.set('sort_by', params.sortBy);
  if (params.facets) {
    Object.entries(params.facets).forEach(([k, v]) => {
      if (v) q.set(k, v);
    });
  }
  q.set('page', '1');
  q.set('page_size', '50');
  return `/products?${q.toString()}`;
}

export default function Market() {
  const { profileId, isManaged } = useProfile();
  const [selectedCategoryId, setSelectedCategoryId] = useState('notebook');
  const [selectedProductId, setSelectedProductId] = useState<string | null>(null);

  // Search & Filter states
  const [search, setSearch] = useState('');
  const [minPrice, setMinPrice] = useState('');
  const [maxPrice, setMaxPrice] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [activeFacets, setActiveFacets] = useState<Record<string, string>>({});
  const [sortBy, setSortBy] = useState('name_asc');

  // Collapsible section states
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [marketStatsOpen, setMarketStatsOpen] = useState(true);
  const [listingsOpen, setListingsOpen] = useState(true);

  const hasActiveFilters = Boolean(
    search || minPrice || maxPrice || selectedTags.length > 0 || Object.values(activeFacets).some(Boolean) || sortBy !== 'name_asc'
  );

  const toggleTag = (tagId: string) => {
    setSelectedTags((prev) =>
      prev.includes(tagId) ? prev.filter((t) => t !== tagId) : [...prev, tagId]
    );
    setSelectedProductId(null);
  };

  const clearFilters = () => {
    setSearch('');
    setMinPrice('');
    setMaxPrice('');
    setSelectedTags([]);
    setActiveFacets({});
    setSortBy('name_asc');
  };

  const handleFacetChange = (key: string, value: string) => {
    setActiveFacets((prev) => ({
      ...prev,
      [key]: value,
    }));
  };

  const { data: productsData, isLoading: productsLoading, error: productsError } = useQuery<ProductResponse>({
    queryKey: ['products', profileId, selectedCategoryId, search, minPrice, maxPrice, selectedTags, activeFacets, sortBy],
    queryFn: () =>
      fetcher<ProductResponse>(
        productsUrl({
          category: selectedCategoryId,
          search,
          minPrice,
          maxPrice,
          tags: selectedTags,
          facets: activeFacets,
          sortBy,
        })
      ),
    enabled: !isManaged || Boolean(profileId),
  });

  const products = productsData?.items || [];

  const selectedProduct = selectedProductId && products.some((product) => product.id === selectedProductId)
    ? selectedProductId
    : products[0]?.id || null;

  const currentProduct = products.find((p) => p.id === selectedProduct);

  const { data: marketData, isLoading: marketLoading, error: marketError } = useQuery<MarketData>({
    queryKey: ['market', profileId, selectedProduct],
    queryFn: () => fetcher<MarketData>(`/products/${encodeURIComponent(selectedProduct || '')}/market`),
    enabled: Boolean(selectedProduct) && (!isManaged || Boolean(profileId)),
  });

  const { data: listingsData, isLoading: listingsLoading } = useQuery<ListingResponse>({
    queryKey: ['product-listings', profileId, selectedProduct, minPrice, maxPrice],
    queryFn: () => {
      const q = new URLSearchParams({
        product_id: selectedProduct || '',
        page: '1',
        page_size: '50',
      });
      if (minPrice !== undefined && minPrice !== '') q.set('min_price', String(minPrice));
      if (maxPrice !== undefined && maxPrice !== '') q.set('max_price', String(maxPrice));
      return fetcher<ListingResponse>(`/listings?${q.toString()}`);
    },
    enabled: Boolean(selectedProduct) && (!isManaged || Boolean(profileId)),
  });

  const listings = listingsData?.items || [];

  const selectCategory = (categoryId: string) => {
    setSelectedCategoryId(categoryId);
    setSelectedProductId(null);
    setActiveFacets({});
  };

  return (
    <div className="flex min-h-full flex-col space-y-6 p-8">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-slate-900">
            <Activity className="text-slate-400" />
            Análise de Mercado
          </h1>
          <p className="mt-1 text-xs text-slate-500">
            Catálogo canônico unificado, medianas de precificação e evidências de mercado em tempo real.
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

      {/* Main Workbench Layout: 3 Columns (Categories & Tags Sidebar | Unified Products | Details/Analytics) */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left Column: Vertical Categories & Interactive Tag Cloud */}
        <aside className="space-y-5 lg:col-span-3">
          {/* Vertical Categories Menu */}
          <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-xs">
            <CategoryStrip
              selectedCategoryId={selectedCategoryId}
              onSelectCategory={selectCategory}
              orientation="vertical"
            />
          </div>

          {/* Interactive Quick Tags (Approximate Filters) */}
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-xs space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-800 uppercase tracking-wider">
                <Tag size={13} className="text-slate-500" />
                <span>Tags Rápidas</span>
              </div>
              {selectedTags.length > 0 && (
                <button
                  type="button"
                  onClick={() => setSelectedTags([])}
                  className="text-[11px] text-slate-400 hover:text-slate-700 transition-colors"
                >
                  Limpar
                </button>
              )}
            </div>

            <p className="text-[11px] text-slate-500 leading-tight">
              Filtre por especificações e hardware de maneira aproximada:
            </p>

            <div className="flex flex-wrap gap-1.5 pt-1">
              {QUICK_TAGS.map((tag) => {
                const isActive = selectedTags.includes(tag.id);
                return (
                  <button
                    key={tag.id}
                    type="button"
                    onClick={() => toggleTag(tag.id)}
                    className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                      isActive
                        ? 'bg-slate-900 text-white shadow-xs'
                        : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                    }`}
                  >
                    <span>{tag.label}</span>
                    {isActive && <X size={11} className="ml-0.5" />}
                  </button>
                );
              })}
            </div>
          </div>
        </aside>

        {/* Center Column: Search & Unified Product List */}
        <section
          className="flex flex-col rounded-lg border border-slate-200 bg-white shadow-xs lg:col-span-4"
          aria-label="Lista de produtos"
        >
          {/* Top Search & Filter Bar */}
          <div className="border-b border-slate-200 p-3 space-y-3 bg-slate-50/50 rounded-t-lg">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-800">Filtros de Busca</span>
              <button
                type="button"
                onClick={() => setFiltersOpen(!filtersOpen)}
                aria-label={filtersOpen ? 'Recolher filtros' : 'Expandir filtros'}
                className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 p-1"
                aria-expanded={filtersOpen}
              >
                {filtersOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                <span>{filtersOpen ? 'Recolher' : 'Expandir'}</span>
              </button>
            </div>

            {filtersOpen && (
              <>
                {/* Search Input */}
                <div>
                  <input
                    id="market-search"
                    type="text"
                    placeholder="Ex: Lenovo LOQ, Dell, Vivobook..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-900 placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                  />
                </div>

            {/* Price Range & Sort */}
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <input
                  type="number"
                  min="0"
                  step="100"
                  placeholder="Min R$"
                  value={minPrice}
                  onChange={(e) => setMinPrice(e.target.value)}
                  className="w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-900 placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </div>
              <div>
                <input
                  type="number"
                  min="0"
                  step="100"
                  placeholder="Max R$"
                  value={maxPrice}
                  onChange={(e) => setMaxPrice(e.target.value)}
                  className="w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-900 placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </div>
              <div>
                <label htmlFor="market-sort-by" className="sr-only">
                  Ordenar por
                </label>
                <select
                  id="market-sort-by"
                  aria-label="Ordenar por"
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value)}
                  className="w-full rounded border border-slate-200 bg-white px-1.5 py-1 text-xs text-slate-900 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                >
                  <option value="name_asc">Nome (A - Z)</option>
                  <option value="name_desc">Nome (Z - A)</option>
                  <option value="price_asc">Menor preço</option>
                  <option value="price_desc">Maior preço</option>
                  <option value="listings_desc">Mais ativos</option>
                </select>
              </div>
            </div>

            {/* Dynamic Category Specific Facets */}
            {filtersOpen && selectedCategoryId !== 'all' && (
              <FacetFilters
                category={selectedCategoryId}
                activeFacets={activeFacets}
                onFacetChange={handleFacetChange}
                onClearFacets={() => setActiveFacets({})}
              />
            )}
              </>
            )}
          </div>

          {/* Unified Product Cards List */}
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2 text-xs text-slate-500">
            <span className="font-semibold text-slate-800">
              Produtos ({productsData?.total ?? products.length})
            </span>
            {selectedCategoryId === 'other' && (
              <span className="text-[11px] text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded">
                Classificação ampla
              </span>
            )}
          </div>

          <div className="flex-1 space-y-1 overflow-auto p-2 max-h-[640px]">
            {productsLoading ? (
              <div className="p-6 text-center text-xs text-slate-500">Carregando produtos...</div>
            ) : productsError ? (
              <div className="p-6 text-center text-xs text-rose-600">Falha ao carregar produtos.</div>
            ) : products.length === 0 ? (
              <div className="p-8 text-center text-xs text-slate-400">
                Nenhum produto encontrado para estes filtros.
              </div>
            ) : (
              products.map((product) => {
                const isSelected = selectedProduct === product.id;
                const price =
                  product.market_median_price ||
                  product.attributes?.reference_price_brl ||
                  0;
                const attrs = product.attributes || {};
                const cpuLabel = attrs.cpu_model || attrs.chip || attrs.cpu || '';
                const ramLabel = attrs.ram_gb ? `${attrs.ram_gb}GB` : '';
                const panelLabel = attrs.panel_type || (attrs.screen_ips ? 'IPS' : '');

                return (
                  <button
                    type="button"
                    key={product.id}
                    aria-pressed={isSelected}
                    onClick={() => setSelectedProductId(product.id)}
                    className={`w-full rounded-md border p-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                      isSelected
                        ? 'border-slate-900 bg-slate-900 text-white shadow-xs'
                        : 'border-transparent text-slate-700 hover:bg-slate-100'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div
                        className={`font-medium text-xs leading-snug ${
                          isSelected ? 'text-white' : 'text-slate-900'
                        }`}
                      >
                        {product.display_name}
                      </div>
                      {product.source_url && (
                        <a
                          href={canonicalSourceUrl(product.source_url)}
                          target="_blank"
                          rel="noreferrer noopener"
                          referrerPolicy="no-referrer"
                          onClick={(e) => e.stopPropagation()}
                          title="Abrir link original"
                          className={`p-0.5 rounded transition-colors shrink-0 ${
                            isSelected ? 'text-slate-300 hover:text-white' : 'text-slate-400 hover:text-slate-800'
                          }`}
                          aria-label={`Abrir link de ${product.display_name}`}
                        >
                          <ExternalLink size={13} />
                        </a>
                      )}
                    </div>

                    {/* Specs resumo */}
                    {(cpuLabel || ramLabel || panelLabel) && (
                      <div
                        className={`mt-1 flex flex-wrap items-center gap-1.5 text-[11px] ${
                          isSelected ? 'text-slate-300' : 'text-slate-500'
                        }`}
                      >
                        {cpuLabel && <span>{cpuLabel}</span>}
                        {cpuLabel && (ramLabel || panelLabel) && <span>·</span>}
                        {ramLabel && <span>{ramLabel}</span>}
                        {ramLabel && panelLabel && <span>·</span>}
                        {panelLabel && <span>{panelLabel}</span>}
                      </div>
                    )}

                    {/* Preço e contagem visíveis diretamente no card */}
                    <div
                      className={`mt-2 flex items-center justify-between border-t pt-1.5 ${
                        isSelected ? 'border-slate-800' : 'border-slate-100'
                      }`}
                    >
                      <span className={`font-semibold text-xs ${isSelected ? 'text-emerald-300' : 'text-slate-900'}`}>
                        {price > 0 ? formatBRL(price) : 'Preço indisponível'}
                      </span>
                      <div
                        className={`flex items-center gap-1.5 text-[10px] ${
                          isSelected ? 'text-slate-300' : 'text-slate-500'
                        }`}
                      >
                        <span>{translateCategory(product.category)}</span>
                        <span>·</span>
                        <span>
                          {product.active_listings_count && product.active_listings_count > 0
                            ? `${product.active_listings_count} ativos`
                            : 'Catálogo'}
                        </span>
                      </div>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </section>

        {/* Right Column: Detalhes, Análise de Mercado e Anúncios Reais */}
        <section
          className="space-y-6 overflow-auto rounded-lg border border-slate-200 bg-white p-6 shadow-xs lg:col-span-5 min-h-[640px]"
          aria-live="polite"
        >
          {!selectedProduct || !currentProduct ? (
            <div className="flex h-full items-center justify-center text-xs text-slate-400">
              Selecione um produto para visualizar a análise de mercado e anúncios.
            </div>
          ) : marketLoading ? (
            <div className="animate-pulse p-8 text-xs text-slate-500">Carregando dados de mercado...</div>
          ) : marketError || !marketData ? (
            <div className="p-8 text-xs text-rose-600">Falha ao carregar dados de mercado.</div>
          ) : (
            <div className="space-y-6">
              {/* Product Header */}
              <div className="border-b border-slate-100 pb-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900">{currentProduct.display_name}</h2>
                    <div className="mt-1 text-xs text-slate-500">
                      {translateCategory(currentProduct.category)}
                      {currentProduct.brand ? ` · ${currentProduct.brand}` : ''}
                      {currentProduct.family ? ` · ${currentProduct.family}` : ''}
                    </div>
                  </div>

                  {currentProduct.source_url && (
                    <a
                      href={canonicalSourceUrl(currentProduct.source_url)}
                      target="_blank"
                      rel="noreferrer noopener"
                      referrerPolicy="no-referrer"
                      aria-label="Acessar link do produto"
                      className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 hover:text-slate-900 transition-colors"
                    >
                      <ExternalLink size={13} />
                      <span>Acessar link do produto</span>
                    </a>
                  )}
                </div>

                {/* Especificações do Catálogo */}
                {currentProduct.attributes && Object.keys(currentProduct.attributes).length > 0 && (
                  <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 rounded-md bg-slate-50 p-2.5 text-[11px]">
                    {currentProduct.attributes.cpu_model && (
                      <div>
                        <span className="text-slate-400 block">Processador</span>
                        <span className="font-medium text-slate-800">{currentProduct.attributes.cpu_model}</span>
                      </div>
                    )}
                    {currentProduct.attributes.ram_gb && (
                      <div>
                        <span className="text-slate-400 block">Memória RAM</span>
                        <span className="font-medium text-slate-800">
                          {currentProduct.attributes.ram_gb} GB {currentProduct.attributes.ram_type || ''}
                        </span>
                      </div>
                    )}
                    {currentProduct.attributes.storage_gb && (
                      <div>
                        <span className="text-slate-400 block">Armazenamento</span>
                        <span className="font-medium text-slate-800">
                          {currentProduct.attributes.storage_gb} GB {currentProduct.attributes.storage_type || ''}
                        </span>
                      </div>
                    )}
                    {(currentProduct.attributes.panel_type || currentProduct.attributes.screen_resolution) && (
                      <div>
                        <span className="text-slate-400 block">Tela / Painel</span>
                        <span className="font-medium text-slate-800">
                          {currentProduct.attributes.panel_type || (currentProduct.attributes.screen_ips ? 'IPS' : 'TN')}{' '}
                          {currentProduct.attributes.screen_resolution ? `(${currentProduct.attributes.screen_resolution})` : ''}
                        </span>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {marketData.market_eligible === false ? (
                <section className="rounded-lg border border-amber-200 bg-amber-50 p-4" aria-label="Sem coorte de precificação">
                  <h3 className="text-xs font-semibold text-amber-950">Sem coorte de precificação</h3>
                  <p className="mt-1 text-xs leading-5 text-amber-900">
                    {marketData.market_exclusion_reason || 'Este item foi mantido na coleta, mas não possui uma coorte comparável para análise de preço.'}
                  </p>
                </section>
              ) : (
                /* Sub-section: Análise de Mercado (Collapsible) */
                <div className="rounded-lg border border-slate-200 bg-white shadow-xs">
                  <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
                    <h3 className="font-medium text-slate-900 text-xs uppercase tracking-wider">Estatísticas de Precificação</h3>
                    <button
                      type="button"
                      onClick={() => setMarketStatsOpen(!marketStatsOpen)}
                      className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 p-1"
                      aria-expanded={marketStatsOpen}
                    >
                      {marketStatsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      <span>{marketStatsOpen ? 'Recolher' : 'Expandir'}</span>
                    </button>
                  </div>

                  {marketStatsOpen && (
                    <div className="p-4 space-y-5">
                      <div className="flex flex-wrap items-center gap-3 text-xs">
                        <span
                          className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-medium ${
                            marketData.heat_band === 'HOT'
                              ? 'bg-orange-100 text-orange-800'
                              : marketData.heat_band === 'WARM'
                              ? 'bg-amber-100 text-amber-800'
                              : 'bg-slate-100 text-slate-800'
                          }`}
                        >
                          Aquecimento: {marketData.heat_band || '-'}{' '}
                          {marketData.market_heat === undefined ? '' : `(${marketData.market_heat.toFixed(1)})`}
                        </span>
                        <span className="text-slate-500">Amostra: {marketData.sample_size ?? '-'}</span>
                        <span className="text-slate-500">
                          Confiança: {marketData.confidence === undefined ? '-' : formatPercent(marketData.confidence, 0)}
                        </span>
                      </div>

                      {/* Coorte e Composição da Amostra */}
                      <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-[11px] space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-2">
                          <div className="flex items-center gap-1.5 font-medium text-slate-900">
                            <Layers size={13} className="text-slate-500" />
                            <span>Coorte: {marketData.market_cohort_name || currentProduct.display_name}</span>
                          </div>
                          <span className="text-slate-400">
                            {marketData.taxonomy_version || 'hardware-taxonomy-v2'}
                          </span>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1">
                          <div className="flex items-center gap-1 text-emerald-700">
                            <CheckCircle2 size={12} />
                            <span>Confirmadas: <strong>{marketData.included_count ?? marketData.active_count ?? 0}</strong></span>
                          </div>
                          <div className="flex items-center gap-1 text-amber-700">
                            <AlertCircle size={12} />
                            <span>Não Verificadas: <strong>{marketData.unverified_count ?? 0}</strong></span>
                          </div>
                          <div className="flex items-center gap-1 text-slate-600">
                            <XCircle size={12} />
                            <span>Excluídas: <strong>{marketData.excluded_count ?? 0}</strong></span>
                          </div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                        <ValueCard title="Mediana anunciada" value={formatBRL(marketData.asking_median)} />
                        <ValueCard
                          title="Estimativa de liquidação"
                          value={formatBRL(marketData.estimated_clearing_value)}
                          highlighted
                        />
                        <ValueCard title="Valor de venda rápida" value={formatBRL(marketData.fast_sale_value)} />
                      </div>

                      <div>
                        <h4 className="mb-2 text-[11px] font-semibold text-slate-500 uppercase tracking-wide">
                          Percentis de preço
                        </h4>
                        <div className="grid grid-cols-5 gap-1.5 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs">
                          <Percentile label="P10" value={marketData.p10} />
                          <Percentile label="P25" value={marketData.p25} />
                          <Percentile label="Mediana" value={marketData.median} emphasized />
                          <Percentile label="P75" value={marketData.p75} />
                          <Percentile label="P90" value={marketData.p90} />
                        </div>
                        {marketData.mad !== undefined && marketData.mad > 0 && (
                          <div className="mt-1.5 text-right text-[11px] text-slate-400">
                            MAD: {formatBRL(marketData.mad)}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Sub-section: Anúncios Disponíveis */}
              <div className="rounded-lg border border-slate-200 bg-white shadow-xs">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium text-slate-900 text-xs uppercase tracking-wider">Anúncios Coletados</h3>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600 font-medium">
                      {listings.length}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setListingsOpen(!listingsOpen)}
                    className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 p-1"
                    aria-expanded={listingsOpen}
                  >
                    {listingsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    <span>{listingsOpen ? 'Recolher' : 'Expandir'}</span>
                  </button>
                </div>

                {listingsOpen && (
                  <div className="p-3">
                    {listingsLoading ? (
                      <div className="p-4 text-xs text-slate-500">Carregando anúncios...</div>
                    ) : listings.length === 0 ? (
                      <div className="rounded-md border border-dashed border-slate-200 p-5 text-center">
                        <p className="text-xs text-slate-500">
                          Nenhum anúncio secundário capturado para este modelo no momento.
                        </p>
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs text-left">
                          <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 text-[11px]">
                            <tr>
                              <th className="px-2.5 py-2 font-medium">Título do anúncio</th>
                              <th className="px-2.5 py-2 font-medium">Local</th>
                              <th className="px-2.5 py-2 font-medium">Condição</th>
                              <th className="px-2.5 py-2 font-medium">Preço</th>
                              <th className="px-2.5 py-2 font-medium">Status</th>
                              <th className="px-2.5 py-2 text-center font-medium">Acesso</th>
                            </tr>
                          </thead>
                          <tbody>
                            {listings.map((listing) => (
                              <tr key={listing.id} className="border-b border-slate-100 hover:bg-slate-50">
                                <td className="px-2.5 py-2 font-medium text-slate-900 max-w-[180px] truncate" title={listing.title}>
                                  {listing.title}
                                </td>
                                <td className="px-2.5 py-2 text-slate-500 text-[11px]">
                                  {listing.location_state || '-'}
                                </td>
                                <td className="px-2.5 py-2 text-slate-600 capitalize text-[11px]">
                                  {listing.condition ? translateCondition(listing.condition) : 'Não informado'}
                                </td>
                                <td className="px-2.5 py-2 font-semibold text-slate-900">
                                  {listing.price == null ? 'Sem preço' : formatBRL(listing.price)}
                                </td>
                                <td className="px-2.5 py-2">
                                  <span
                                    className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                                      listing.status === 'active'
                                        ? 'bg-emerald-100 text-emerald-800'
                                        : 'bg-slate-100 text-slate-800'
                                    }`}
                                  >
                                    {translateStatus(listing.status)}
                                  </span>
                                </td>
                                <td className="px-2.5 py-2 text-center">
                                  {listing.source_url ? (
                                    <a
                                      href={canonicalSourceUrl(listing.source_url)}
                                      target="_blank"
                                      rel="noreferrer noopener"
                                      referrerPolicy="no-referrer"
                                      className="inline-flex items-center gap-1 rounded bg-slate-100 hover:bg-slate-900 hover:text-white px-2 py-0.5 text-[11px] font-medium text-slate-700 transition-colors"
                                      aria-label={`Acessar link do anúncio ${listing.title}`}
                                    >
                                      <ExternalLink size={12} />
                                      <span>Ver</span>
                                    </a>
                                  ) : (
                                    <span className="text-[11px] text-slate-400">—</span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function ValueCard({
  title,
  value,
  highlighted = false,
}: {
  title: string;
  value: string;
  highlighted?: boolean;
}) {
  return (
    <div className={`rounded-lg border border-slate-200 p-3.5 ${highlighted ? 'bg-slate-50' : ''}`}>
      <div className="mb-1 text-xs text-slate-500 font-medium">{title}</div>
      <div className="text-xl font-semibold text-slate-900">{value}</div>
    </div>
  );
}

function Percentile({
  label,
  value,
  emphasized = false,
}: {
  label: string;
  value?: number;
  emphasized?: boolean;
}) {
  return (
    <div className="text-center">
      <div className="mb-1 text-slate-400 text-[10px]">{label}</div>
      <div className={emphasized ? 'font-semibold text-slate-900 text-xs' : 'font-medium text-slate-700 text-xs'}>
        {formatBRL(value)}
      </div>
    </div>
  );
}
