import { useQuery } from '@tanstack/react-query';
import { fetcher } from '../api';
import { useProfile } from '../profile';
import { Filter, X } from 'lucide-react';

export interface FacetOption {
  value: string | number;
  label: string;
}

export interface CategoryFacet {
  key: string;
  label: string;
  type: 'select' | 'boolean' | 'range' | 'text';
  options?: FacetOption[];
}

export interface FacetsResponse {
  category: string;
  category_label: string;
  facets: CategoryFacet[];
}

interface FacetFiltersProps {
  category: string;
  activeFacets: Record<string, string>;
  onFacetChange: (key: string, value: string) => void;
  onClearFacets: () => void;
}

export function FacetFilters({
  category,
  activeFacets,
  onFacetChange,
  onClearFacets,
}: FacetFiltersProps) {
  const { profileId, isManaged } = useProfile();

  const { data } = useQuery<FacetsResponse>({
    queryKey: ['category-facets', profileId, category],
    queryFn: () => fetcher<FacetsResponse>(`/products/facets?category=${encodeURIComponent(category)}`),
    enabled: (!isManaged || Boolean(profileId)) && Boolean(category) && category !== 'all',
    staleTime: 120_000,
  });

  if (!category || category === 'all' || !data || !data.facets?.length) {
    return null;
  }

  const activeCount = Object.values(activeFacets).filter(Boolean).length;

  return (
    <div className="rounded-md border border-slate-200 bg-slate-50/70 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-700 uppercase tracking-wider">
          <Filter size={13} className="text-slate-500" />
          <span>Filtros Específicos: {data.category_label}</span>
          {activeCount > 0 && (
            <span className="rounded-full bg-slate-900 px-1.5 py-0.2 text-[10px] font-bold text-white">
              {activeCount}
            </span>
          )}
        </div>
        {activeCount > 0 && (
          <button
            type="button"
            onClick={onClearFacets}
            className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 transition-colors"
          >
            <X size={12} />
            <span>Limpar filtros de {data.category_label}</span>
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {data.facets.map((facet) => {
          const currentValue = activeFacets[facet.key] || '';
          return (
            <div key={facet.key} className="space-y-1">
              <label htmlFor={`facet-${facet.key}`} className="block text-xs font-medium text-slate-600">
                {facet.label}
              </label>
              {facet.options && facet.options.length > 0 ? (
                <select
                  id={`facet-${facet.key}`}
                  value={currentValue}
                  onChange={(e) => onFacetChange(facet.key, e.target.value)}
                  className="w-full rounded border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-900 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                >
                  <option value="">Todos</option>
                  {facet.options.map((opt) => (
                    <option key={String(opt.value)} value={String(opt.value)}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id={`facet-${facet.key}`}
                  type="text"
                  placeholder={`Buscar ${facet.label.toLowerCase()}...`}
                  value={currentValue}
                  onChange={(e) => onFacetChange(facet.key, e.target.value)}
                  className="w-full rounded border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-900 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default FacetFilters;
