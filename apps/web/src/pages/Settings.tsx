import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetcher } from '../api';
import { Settings as SettingsIcon } from 'lucide-react';
import { toast } from 'sonner';
import { MarketplaceAuthCard } from '../components/MarketplaceAuthCard';
import { FieldTip } from '../components/ui/FieldTip';
import { useProfile } from '../profile';

export default function Settings() {
  const { profileId, isManaged } = useProfile();
  const queryClient = useQueryClient();
  
  const { data, isLoading } = useQuery({
    queryKey: ['settings', profileId],
    queryFn: () => fetcher<any>('/settings'),
    enabled: !isManaged || Boolean(profileId),
  });

  const { data: scopesData } = useQuery<any>({
    queryKey: ['search-scopes', profileId],
    queryFn: () => fetcher<any>('/search-scopes?marketplace=olx'),
    enabled: !isManaged || Boolean(profileId),
  });

  const [formData, setFormData] = useState<any>(null);

  useEffect(() => {
    if (data?.preference_profile) {
      setFormData(data.preference_profile);
    }
  }, [data]);

  const mutation = useMutation({
    mutationFn: (newPrefs: any) => fetcher('/settings', { 
      method: 'PUT', 
      body: JSON.stringify({ preference_profile: newPrefs }) 
    }),
    onSuccess: () => {
      toast.success('Preferências salvas com sucesso');
      queryClient.invalidateQueries({ queryKey: ['settings', profileId] });
    },
    onError: () => {
      toast.error('Falha ao salvar preferências');
    }
  });

  const [newScope, setNewScope] = useState({ name: '', query: '', category: 'gpu', max_price: 3000, limit: 10 });
  const scopeMutation = useMutation({
    mutationFn: () => fetcher('/search-scopes', {
      method: 'POST',
      body: JSON.stringify({ ...newScope, marketplace: 'olx', sort: 'recent', enabled: true }),
    }),
    onSuccess: () => {
      toast.success('Escopo criado');
      setNewScope({ name: '', query: '', category: 'gpu', max_price: 3000, limit: 10 });
      queryClient.invalidateQueries({ queryKey: ['search-scopes', profileId] });
    },
    onError: () => toast.error('Falha ao criar escopo'),
  });

  const disableScopeMutation = useMutation({
    mutationFn: (id: string) => fetcher(`/search-scopes/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['search-scopes', profileId] }),
    onError: () => toast.error('Falha ao desabilitar escopo'),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (formData) {
      mutation.mutate(formData);
    }
  };

  if (isLoading || !formData) return <div className="p-8 text-slate-500">Carregando configurações...</div>;

  return (
    <div className="p-8 space-y-6 max-w-4xl">
      <h1 className="text-2xl font-semibold text-slate-900 flex items-center gap-2">
        <SettingsIcon className="text-slate-400" />
        Configurações & Sessão de Marketplace
      </h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-6">
          <MarketplaceAuthCard />

          <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium text-slate-900">Status do Sistema</h2>
              <FieldTip content="Status geral da infraestrutura e integrações externas ativas." size={14} />
            </div>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between items-center">
                <span className="text-slate-500 flex items-center gap-1">
                  Modo da Aplicação
                  <FieldTip content="Modo de operação: 'Local' com fixtures ou 'Ao vivo' com extração real." size={12} />
                </span>
                <span className="font-medium text-slate-900 capitalize">
                  {data.app_mode === 'live' ? 'Ao vivo' : 'Local'}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-500 flex items-center gap-1">
                  Dados Sintéticos
                  <FieldTip content="Indica se o gerador de dados sintéticos está ativado para testes locais." size={12} />
                </span>
                <span className="font-medium text-slate-900">{data.synthetic_data ? 'Ativado' : 'Desativado'}</span>
              </div>
              <div className="pt-3 border-t border-slate-100">
                <div className="font-medium text-slate-900 mb-2 flex items-center gap-1">
                  Provedores
                  <FieldTip content="Status de disponibilidade de serviços de IA, busca vetorial e scraping." size={12} />
                </div>
                {Object.entries(data.external_providers || {}).map(([key, value]) => (
                  <div key={key} className="flex justify-between items-center py-1 text-xs">
                    <span className="text-slate-500">{key}</span>
                    <span className={`font-medium ${value === 'disabled' ? 'text-slate-400' : 'text-emerald-600'}`}>
                      {value === 'disabled' ? 'Desativado' : (value as string)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-medium text-slate-900">Preferências do Usuário</h2>
            <FieldTip content="Critérios de investimento e parâmetros para detecção automática de oportunidades." size={14} />
          </div>
          <form onSubmit={handleSubmit} className="space-y-4 text-sm">
            <div>
              <label className="block text-slate-700 mb-1 flex items-center gap-1">
                Capital Máximo (R$)
                <FieldTip content="Valor máximo em Reais disponível para alocação em oportunidades." size={12} />
              </label>
              <input 
                type="number" 
                value={formData.max_capital} 
                onChange={e => setFormData({...formData, max_capital: Number(e.target.value)})}
                className="w-full border border-slate-300 rounded px-3 py-2 focus:border-accent outline-none"
              />
            </div>
            <div>
              <label className="block text-slate-700 mb-1 flex items-center gap-1">
                Margem Mínima Desejada
                <FieldTip content="Desconto mínimo exigido em relação ao valor de mercado (ex: 0.10 para 10%)." size={12} />
              </label>
              <input 
                type="number" 
                step="0.01"
                value={formData.min_desired_edge} 
                onChange={e => setFormData({...formData, min_desired_edge: Number(e.target.value)})}
                className="w-full border border-slate-300 rounded px-3 py-2 focus:border-accent outline-none"
              />
            </div>
            <div>
              <label className="block text-slate-700 mb-1 flex items-center gap-1">
                Tolerância ao Risco (0 a 1)
                <FieldTip content="Nível de tolerância à incerteza dos dados (0 = ultraconservador, 1 = arrojado)." size={12} />
              </label>
              <input 
                type="number" 
                step="0.01"
                max="1"
                min="0"
                value={formData.risk_tolerance} 
                onChange={e => setFormData({...formData, risk_tolerance: Number(e.target.value)})}
                className="w-full border border-slate-300 rounded px-3 py-2 focus:border-accent outline-none"
              />
            </div>
            <div>
              <label className="block text-slate-700 mb-1 flex items-center gap-1">
                Localização Preferida
                <FieldTip content="Sigla do estado ou cidade prioritária para filtrar anúncios (ex: SP, RJ, MG)." size={12} />
              </label>
              <input 
                type="text" 
                value={formData.preferred_location} 
                onChange={e => setFormData({...formData, preferred_location: e.target.value})}
                className="w-full border border-slate-300 rounded px-3 py-2 focus:border-accent outline-none"
              />
            </div>
            <div className="pt-2">
              <button 
                type="submit" 
                disabled={mutation.isPending}
                className="w-full bg-slate-900 text-white rounded px-4 py-2 hover:bg-slate-800 disabled:opacity-50 transition-colors shadow-sm font-medium"
              >
                {mutation.isPending ? 'Salvando...' : 'Salvar Preferências'}
              </button>
            </div>
          </form>
        </div>
      </div>

      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-medium text-slate-900">Escopos salvos da OLX</h2>
            <p className="text-sm text-slate-500 mt-1">Um escopo representa um produto e seus filtros. Ele só é executado quando selecionado na tela de Pipelines.</p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-2 mb-4">
          <input value={newScope.name} onChange={e => setNewScope({ ...newScope, name: e.target.value })} placeholder="Nome" className="border border-slate-300 rounded px-3 py-2 text-sm" />
          <input value={newScope.query} onChange={e => setNewScope({ ...newScope, query: e.target.value })} placeholder="Consulta OLX" className="border border-slate-300 rounded px-3 py-2 text-sm" />
          <select value={newScope.category} onChange={e => setNewScope({ ...newScope, category: e.target.value })} className="border border-slate-300 rounded px-3 py-2 text-sm bg-white">
            <option value="gpu">GPU</option><option value="notebook">Notebook</option><option value="cpu">CPU</option><option value="ram">RAM</option><option value="ssd">SSD</option><option value="motherboard">Placa-mãe</option><option value="desktop">Desktop</option><option value="other">Outro</option>
          </select>
          <input type="number" value={newScope.max_price} onChange={e => setNewScope({ ...newScope, max_price: Number(e.target.value) })} placeholder="Preço máximo" className="border border-slate-300 rounded px-3 py-2 text-sm" />
          <button disabled={!newScope.name.trim() || !newScope.query.trim() || scopeMutation.isPending} onClick={() => scopeMutation.mutate()} className="bg-slate-900 text-white rounded px-3 py-2 text-sm disabled:opacity-50">Adicionar escopo</button>
        </div>
        <div className="divide-y divide-slate-100">
          {(scopesData?.items || []).map((scope: any) => (
            <div key={scope.id} className="flex items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <div className="font-medium text-sm text-slate-900 truncate">{scope.name} {!scope.enabled && <span className="text-xs text-slate-400">(desabilitado)</span>}</div>
                <div className="text-xs text-slate-500 truncate">{scope.query} · {scope.category || 'todas'} · até R$ {scope.max_price ?? '—'} · {scope.limit} anúncios</div>
              </div>
              {scope.enabled && <button onClick={() => disableScopeMutation.mutate(scope.id)} className="text-xs text-red-600 hover:underline shrink-0">Desabilitar</button>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
