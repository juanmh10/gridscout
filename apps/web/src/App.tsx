import { useState } from 'react';
import { BrowserRouter as Router, Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { LayoutDashboard, Target, Activity, List, Play, BarChart, Settings, BrainCircuit, Search, Database } from 'lucide-react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useQuery } from '@tanstack/react-query';
import { fetcher } from './api';
import { Toaster } from 'sonner';

import Dashboard from './pages/Dashboard';
import Opportunities from './pages/Opportunities';
import Market from './pages/Market';
import Listings from './pages/Listings';
import Pipelines from './pages/Pipelines';
import PipelineChat from './pages/PipelineChat';
import Benchmarks from './pages/Benchmarks';
import SettingsPage from './pages/Settings';
import AIMetrics from './pages/AIMetrics';
import Searches from './pages/Searches';
import IncompleteDiscoveries from './pages/IncompleteDiscoveries';
import { ProfileGate, ProfileSwitcher } from './profile';

const queryClient = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  );
}

function AppContent() {
  return (
    <Router>
      <ProfileGate queryClient={queryClient}>
        <AppShell />
      </ProfileGate>
    </Router>
  );
}

function AppShell() {
  const { data: status } = useQuery<any>({
    queryKey: ['status'],
    queryFn: () => fetcher<any>('/status'),
  });
  const location = useLocation();
  const isPipelineChat = /^\/pipelines\/[^/]+\/chat$/.test(location.pathname);
  const isFocusChat = isPipelineChat || location.pathname === '/searches';
  const [chatNavigationExpanded, setChatNavigationExpanded] = useState(false);
  const compactNavigation = isFocusChat && !chatNavigationExpanded;

  return (
    <>
        <div className={`flex h-screen w-full font-sans ${isFocusChat ? 'bg-slate-950 text-slate-100' : 'bg-slate-50 text-slate-900'}`}>
          {/* Sidebar */}
          <aside className={`${compactNavigation ? 'w-[72px]' : 'w-64'} shrink-0 bg-slate-900 text-slate-300 flex flex-col transition-[width] duration-200`}>
            <div className={`p-4 flex items-center border-b border-slate-800 ${compactNavigation ? 'justify-center' : 'gap-2'}`}>
              <Activity className="w-6 h-6 text-accent" />
              {!compactNavigation && <span className="text-xl font-semibold text-slate-50">Market Radar</span>}
            </div>
            <ProfileSwitcher compact={compactNavigation} />
            {isFocusChat && (
              <button type="button" onClick={() => setChatNavigationExpanded(value => !value)} className={`m-2 rounded-md border border-slate-700 px-3 py-2 text-left text-xs text-slate-300 hover:bg-slate-800 ${compactNavigation ? 'px-0 text-center' : ''}`} title={compactNavigation ? 'Expandir navegação' : undefined}>
                {compactNavigation ? 'Menu' : 'Recolher navegação'}
              </button>
            )}

            <nav className="flex-1 p-2 space-y-1">
              <NavItem compact={compactNavigation} to="/" icon={<LayoutDashboard size={20} />} label="Painel Geral" />
              <NavItem compact={compactNavigation} to="/pipelines" icon={<Play size={20} />} label="Pipelines" />
              <NavItem compact={compactNavigation} to="/opportunities" icon={<Target size={20} />} label="Oportunidades" />
              <NavItem compact={compactNavigation} to="/market" icon={<Activity size={20} />} label="Mercado" />
              <NavItem compact={compactNavigation} to="/listings" icon={<List size={20} />} label="Anúncios" />
              <NavItem compact={compactNavigation} to="/incompletos" icon={<Database size={20} />} label="Dados incompletos" />
              <NavItem compact={compactNavigation} to="/searches" icon={<Search size={20} />} label="Buscas" />
              <NavItem compact={compactNavigation} to="/benchmarks" icon={<BarChart size={20} />} label="Benchmarks" />
              <NavItem compact={compactNavigation} to="/ai-metrics" icon={<BrainCircuit size={20} />} label="Métricas de IA" />
            </nav>

            <div className="p-2 border-t border-slate-800 space-y-1">
              <NavItem compact={compactNavigation} to="/settings" icon={<Settings size={20} />} label="Configurações" />
              {!compactNavigation && <div className="px-3 py-2 mt-2 text-[10px] font-bold tracking-wider text-slate-500 uppercase flex items-center justify-between bg-slate-800/50 rounded">
                <span>Modo</span>
                <span className={status?.app_mode === 'live' ? 'text-emerald-400' : 'text-amber-500'}>
                  {status?.app_mode === 'live' ? 'Ao vivo · Leitura' : 'Local · Testes'}
                </span>
              </div>}
            </div>
          </aside>

          {/* Main Content */}
          <main className={`flex-1 min-w-0 ${isFocusChat ? 'overflow-hidden bg-slate-950' : 'overflow-auto bg-slate-50'}`}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/opportunities" element={<Opportunities />} />
              <Route path="/market" element={<Market />} />
              <Route path="/listings" element={<Listings />} />
              <Route path="/incompletos" element={<IncompleteDiscoveries />} />
              <Route path="/pipelines" element={<Pipelines />} />
              <Route path="/pipelines/:pipelineId/chat" element={<PipelineChat />} />
              <Route path="/searches" element={<Searches />} />
              <Route path="/benchmarks" element={<Benchmarks />} />
              <Route path="/ai-metrics" element={<AIMetrics />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </main>
        </div>
        <Toaster position="bottom-right" richColors closeButton />
    </>
  );
}

function NavItem({ to, icon, label, compact = false }: { to: string; icon: React.ReactNode; label: string; compact?: boolean }) {
  return (
    <NavLink
      to={to}
      title={compact ? label : undefined}
      className={({ isActive }) =>
        `flex items-center rounded-md transition-colors ${compact ? 'justify-center px-2 py-2.5' : 'gap-3 px-3 py-2'} ${
          isActive 
            ? 'bg-slate-800 text-slate-50 font-medium' 
            : 'hover:bg-slate-800/50 hover:text-slate-200'
        }`
      }
    >
      {icon}
      {!compact && <span>{label}</span>}
    </NavLink>
  );
}
