import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetcher } from '../api';
import { List, ExternalLink, ShieldCheck, HelpCircle, CircleAlert } from 'lucide-react';
import { formatBRL, translateCategory, translateCondition, translateStatus, canonicalSourceUrl } from '../lib/format';
import { CategoryStrip } from '../components/CategoryStrip';
import { useProfile } from '../profile';

export default function Listings() {
  const { profileId, isManaged } = useProfile();
  const [page, setPage] = useState(1);
  const [selectedCategoryId, setSelectedCategoryId] = useState('all');
  const [qualityFilter, setQualityFilter] = useState('all');
  const [auditFilter, setAuditFilter] = useState('all');

  const query = new URLSearchParams({
    page: String(page),
    page_size: '20',
  });
  if (selectedCategoryId && selectedCategoryId !== 'all') {
    query.set('category', selectedCategoryId);
  }
  if (qualityFilter !== 'all') {
    query.set('quality', qualityFilter);
  }
  if (auditFilter !== 'all') query.set('audit_status', auditFilter);

  const { data, isLoading, error } = useQuery({
    queryKey: ['listings', profileId, page, selectedCategoryId, qualityFilter, auditFilter],
    queryFn: () => fetcher<any>(`/listings?${query.toString()}`),
    enabled: !isManaged || Boolean(profileId),
  });

  const selectCategory = (catId: string) => {
    setSelectedCategoryId(catId);
    setPage(1);
  };

  return (
    <div className="p-8 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 flex items-center gap-2">
            <List className="text-slate-400" />
            Anúncios Observados
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Anúncios capturados do marketplace com classificação de taxonomia e elegibilidade.
          </p>
        </div>

        {/* Quality filter */}
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-600 font-medium">Qualidade:</span>
          <select
            value={qualityFilter}
            onChange={(e) => {
              setQualityFilter(e.target.value);
              setPage(1);
            }}
            className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-900 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="all">Todas as observações</option>
            <option value="confirmed">Apenas Confirmados (Elegíveis)</option>
            <option value="unverified">Apenas Não Verificados</option>
          </select>
          <select
            aria-label="Auditoria"
            value={auditFilter}
            onChange={(e) => { setAuditFilter(e.target.value); setPage(1); }}
            className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-900 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="all">Toda evidência</option>
            <option value="unaudited">Não auditados</option>
            <option value="audited">Auditados</option>
          </select>
        </div>
      </div>

      <CategoryStrip selectedCategoryId={selectedCategoryId} onSelectCategory={selectCategory} />

      <div className="bg-white border border-slate-200 rounded-lg shadow-sm overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-slate-500 text-sm">Carregando anúncios...</div>
        ) : error ? (
          <div className="p-8 text-red-500 text-sm">Erro ao carregar anúncios.</div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-500">
                  <tr>
                    <th className="px-4 py-3 font-medium">Título</th>
                    <th className="px-4 py-3 font-medium">Categoria / Formato</th>
                    <th className="px-4 py-3 font-medium">Evidência</th>
                    <th className="px-4 py-3 font-medium">Condição</th>
                    <th className="px-4 py-3 font-medium">Preço</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 text-center font-medium">Fonte</th>
                  </tr>
                </thead>
                <tbody>
                  {!data?.items?.length ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-slate-500">
                        Nenhum anúncio encontrado com os filtros selecionados.
                      </td>
                    </tr>
                  ) : (
                    data.items.map((listing: any) => {
                      const isConfirmed = listing.classification_status === 'confirmed';
                      const isLegacy = listing.classification_status === 'legacy_unverified';
                      const audited = listing.audit_status === 'audited';
                      return (
                        <tr key={listing.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                          <td className="px-4 py-3">
                            <div className="font-medium text-slate-900 truncate max-w-md" title={listing.title}>
                              {listing.title}
                            </div>
                            <div className="text-xs text-slate-500 mt-0.5">
                              {listing.location_city ? `${listing.location_city}, ${listing.location_state}` : 'Localização não informada'}
                            </div>
                          </td>
                          <td className="px-4 py-3">
                            <div className="text-slate-900 font-medium capitalize">{translateCategory(listing.category)}</div>
                            <div className="text-xs text-slate-500 capitalize">{listing.item_form || 'avulso'}</div>
                          </td>
                          <td className="px-4 py-3">
                            {audited ? (
                              <span className="inline-flex items-center gap-1 rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 border border-emerald-200">
                                <ShieldCheck size={12} />
                                Auditado
                              </span>
                            ) : isConfirmed ? (
                              <span className="inline-flex items-center gap-1 rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 border border-amber-200" title="Evidência limitada ao card da busca">
                                <CircleAlert size={12} />
                                Não auditado
                              </span>
                            ) : isLegacy ? (
                              <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 border border-slate-200">
                                Histórico Legado
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 border border-amber-200">
                                <HelpCircle size={12} />
                                Não verificado
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-slate-600 capitalize">{listing.condition ? translateCondition(listing.condition) : 'Não informado'}</td>
                          <td className="px-4 py-3 font-semibold text-slate-900">{listing.price == null ? 'Não informado' : formatBRL(listing.price)}</td>
                          <td className="px-4 py-3">
                            <span
                              className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${
                                listing.status === 'active' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-800'
                              }`}
                            >
                              {translateStatus(listing.status)}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-center">
                            {listing.source_url && (
                              <a
                                href={canonicalSourceUrl(listing.source_url)}
                                target="_blank"
                                rel="noreferrer noopener"
                                referrerPolicy="no-referrer"
                                title="Abrir anúncio original"
                                className="inline-flex rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                              >
                                <ExternalLink size={15} />
                              </a>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>

            <div className="p-4 border-t border-slate-200 flex items-center justify-between text-sm text-slate-500">
              <div>Total: {data.total} observações</div>
              <div className="flex gap-2">
                <button
                  disabled={page === 1}
                  onClick={() => setPage((p) => p - 1)}
                  className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-50 transition-colors"
                >
                  Anterior
                </button>
                <button
                  disabled={page >= (data.pages || 1)}
                  onClick={() => setPage((p) => p + 1)}
                  className="px-3 py-1 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-50 transition-colors"
                >
                  Próximo
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
