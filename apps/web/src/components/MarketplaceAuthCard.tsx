import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  CircleX,
  Clock3,
  ExternalLink,
  LogIn,
  LogOut,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { toast } from 'sonner';
import { fetcher } from '../api';
import { formatDateTime } from '../lib/format';
import { FieldTip } from './ui/FieldTip';
import { useProfile } from '../profile';

interface AccountSession {
  marketplace: string;
  email: string;
  active: boolean;
  authenticated: boolean;
  authenticated_at: string | null;
  last_verified_at: string | null;
}

interface AuthStatusResponse {
  authenticated: boolean;
  session_state?: string;
  email: string;
  accounts?: AccountSession[];
  marketplace: string;
  authenticated_at: string | null;
  last_verified_at?: string | null;
  last_checked_at?: string | null;
  expires_at: string | null;
  cookies_cached: number;
  probe_cache_age_seconds?: number | null;
  manual_browser_session: boolean;
  human_emulation_active: boolean;
  read_only_scope: string[];
  write_actions?: boolean;
  login_stage: string;
  login_message: string;
  worker_available?: boolean;
  browser_ready?: boolean;
  error_code?: string | null;
  error_message?: string | null;
}

interface ManualLoginResponse {
  status: string;
  message: string;
  read_only_scope: string[];
  write_actions: boolean;
  error_code?: string | null;
}

interface RateLimitResponse {
  state: 'closed' | 'cooldown';
  limit_per_hour: number;
  used: number;
  remaining: number;
  reset_at: string | null;
  retry_after_seconds: number | null;
  min_interval_seconds: number;
  max_interval_seconds: number;
}

function statusPresentation(sessionState: string, authenticated: boolean) {
  if (sessionState === 'blocked') {
    return { label: 'Acesso bloqueado', description: 'A OLX bloqueou temporariamente novas consultas.', className: 'bg-amber-50 text-amber-800 border-amber-200', icon: CircleAlert };
  }
  if (authenticated || sessionState === 'connected') {
    return { label: 'Conta conectada', description: 'A OLX confirmou a sessão ativa.', className: 'bg-emerald-50 text-emerald-700 border-emerald-200', icon: CheckCircle2 };
  }
  switch (sessionState) {
    case 'checking':
      return { label: 'Validando sessão', description: 'Confirmando o acesso na OLX.', className: 'bg-sky-50 text-sky-700 border-sky-200', icon: RefreshCw };
    case 'manual_login':
      return { label: 'Login manual aberto', description: 'Conclua o login no Chrome visível.', className: 'bg-sky-50 text-sky-700 border-sky-200', icon: LogIn };
    case 'expired':
      return { label: 'Sessão expirada', description: 'A OLX solicitou login novamente.', className: 'bg-amber-50 text-amber-800 border-amber-200', icon: Clock3 };
    case 'unavailable':
      return { label: 'Serviço indisponível', description: 'O navegador ou worker da OLX não respondeu.', className: 'bg-rose-50 text-rose-800 border-rose-200', icon: CircleX };
    case 'error':
      return { label: 'Falha na sessão', description: 'A sessão não pôde ser confirmada.', className: 'bg-rose-50 text-rose-800 border-rose-200', icon: CircleX };
    case 'disconnected':
    case 'idle':
      return { label: 'Não conectada', description: 'Abra o login manual para usar a OLX ao vivo.', className: 'bg-slate-100 text-slate-600 border-slate-200', icon: ShieldCheck };
    default:
      return { label: 'Estado não confirmado', description: 'Verifique a sessão antes de iniciar uma consulta OLX.', className: 'bg-slate-100 text-slate-600 border-slate-200', icon: ShieldCheck };
  }
}

export function MarketplaceAuthCard() {
  const { profileId, isManaged } = useProfile();
  const [openingManualLogin, setOpeningManualLogin] = useState(false);
  const queryClient = useQueryClient();
  const { data: authStatus, isLoading } = useQuery<AuthStatusResponse>({
    queryKey: ['marketplace-auth', profileId],
    queryFn: () => fetcher<AuthStatusResponse>('/marketplace/auth/status'),
    refetchInterval: (query) => {
      const data = query.state.data as AuthStatusResponse | undefined;
      const loginInProgress = openingManualLogin || data?.login_stage === 'manual_login' || data?.session_state === 'manual_login';
      return loginInProgress ? 2500 : false;
    },
    refetchOnWindowFocus: true,
    enabled: !isManaged || Boolean(profileId),
  });

  const { data: rateLimit } = useQuery<RateLimitResponse>({
    queryKey: ['marketplace-rate-limit', profileId, 'olx'],
    queryFn: () => fetcher<RateLimitResponse>('/marketplace/rate-limit'),
    enabled: Boolean(authStatus) && (!isManaged || Boolean(profileId)),
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });

  const manualStartMutation = useMutation({
    mutationFn: () => fetcher<ManualLoginResponse>('/marketplace/auth/manual-start?marketplace=olx', { method: 'POST' }),
    onSuccess: (response) => {
      setOpeningManualLogin(true);
      toast.info(response.message);
      queryClient.invalidateQueries({ queryKey: ['marketplace-auth', profileId] });
    },
    onError: (error: any) => toast.error(error?.message || 'Não foi possível abrir o login manual.'),
  });

  const manualCompleteMutation = useMutation({
    mutationFn: () => fetcher<ManualLoginResponse>('/marketplace/auth/manual-complete?marketplace=olx', { method: 'POST' }),
    onSuccess: (response) => {
      setOpeningManualLogin(false);
      toast.success(response.message);
      queryClient.invalidateQueries({ queryKey: ['marketplace-auth', profileId] });
    },
    onError: (error: any) => toast.error(error?.message || 'A OLX ainda não confirmou o login.'),
  });

  const revalidateMutation = useMutation({
    mutationFn: () => fetcher<AuthStatusResponse>('/marketplace/auth/status?force=true'),
    onSuccess: (response) => {
      queryClient.setQueryData(['marketplace-auth', profileId], response);
      toast[response.authenticated ? 'success' : 'warning'](
        response.authenticated ? 'Sessão confirmada pela OLX.' : (response.error_message || 'A OLX não confirmou a sessão.'),
      );
    },
    onError: (error: any) => toast.error(error?.message || 'Não foi possível verificar a sessão.'),
  });

  const logoutMutation = useMutation({
    mutationFn: () => fetcher('/marketplace/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      setOpeningManualLogin(false);
      toast.success('Sessão removida.');
      queryClient.invalidateQueries({ queryKey: ['marketplace-auth', profileId] });
    },
    onError: (error: any) => toast.error(error?.message || 'Não foi possível remover a sessão.'),
  });

  if (isLoading) {
    return <div className="bg-white border border-slate-200 rounded-lg p-6 animate-pulse text-slate-500 text-sm">Carregando sessão do navegador...</div>;
  }

  const sessionState = authStatus?.session_state || (authStatus?.authenticated ? 'connected' : 'disconnected');
  const presentation = statusPresentation(sessionState, Boolean(authStatus?.authenticated));
  const StatusIcon = presentation.icon;
  const activeAccount = authStatus?.accounts?.find((account) => account.active) || authStatus?.accounts?.[0];
  const activeEmail = activeAccount?.email || authStatus?.email || '';
  const manualLoginOpen = sessionState === 'manual_login' || authStatus?.login_stage === 'manual_login' || openingManualLogin;
  const workerUnavailable = authStatus?.worker_available === false
    || sessionState === 'unavailable'
    || ['olx_browser_unavailable', 'olx_browser_start_failed', 'olx_session_probe_failed'].includes(authStatus?.error_code || '');

  const switchAccount = () => {
    logoutMutation.mutate();
  };

  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-slate-700" />
          <h2 className="text-lg font-medium text-slate-900">Sessão OLX</h2>
          <FieldTip content="Abre o Chrome local para login manual e valida a sessão apenas para buscas e leitura de anúncios." size={14} />
        </div>
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${presentation.className}`} aria-live="polite">
          <StatusIcon className={`w-3.5 h-3.5 ${sessionState === 'checking' ? 'animate-spin' : ''}`} />
          {presentation.label}
        </span>
      </div>

      <div className="space-y-1">
        <p className="text-sm font-medium text-slate-900">{activeEmail || (authStatus?.authenticated ? 'Conta OLX conectada' : 'Nenhuma conta OLX registrada')}</p>
        <p className="text-xs text-slate-500 leading-relaxed">{authStatus?.error_message || authStatus?.login_message || presentation.description}</p>
      </div>

      {activeEmail && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <div className="rounded-md bg-slate-50 border border-slate-200 px-3 py-2">
            <span className="block text-slate-500">Conta ativa</span>
            <span className="font-medium text-slate-900 break-all">{activeEmail}</span>
          </div>
          <div className="rounded-md bg-slate-50 border border-slate-200 px-3 py-2">
            <span className="block text-slate-500">Última confirmação</span>
            <span className="font-medium text-slate-900">{formatDateTime(authStatus?.last_verified_at)}</span>
          </div>
        </div>
      )}

      {workerUnavailable && (
        <div className="bg-rose-50 border border-rose-200 rounded p-3 text-xs text-rose-900">
          <p>O Chrome visível da OLX está indisponível. A sessão salva não foi removida.</p>
          <button
            type="button"
            onClick={() => revalidateMutation.mutate()}
            disabled={revalidateMutation.isPending}
            className="mt-2 inline-flex items-center gap-2 rounded border border-rose-300 px-3 py-2 font-medium text-rose-900 hover:bg-rose-100 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${revalidateMutation.isPending ? 'animate-spin' : ''}`} />
            {revalidateMutation.isPending ? 'Reabrindo navegador...' : 'Verificar navegador novamente'}
          </button>
        </div>
      )}

      {!workerUnavailable && (
        !authStatus?.authenticated ? (
          <div className="space-y-3 border-t border-slate-100 pt-4">
          <p className="text-xs text-slate-600 leading-relaxed">
            O GridScout abrirá o Chrome visível na página da OLX. Faça todo o login manualmente no navegador — e-mail, senha, código e verificações. Ao terminar, volte aqui e confirme a sessão.
          </p>
          {manualLoginOpen ? (
            <div className="space-y-3 rounded-md border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900">
              <div className="flex items-start gap-2"><CircleAlert className="mt-0.5 h-4 w-4 shrink-0" /> Conclua o login na janela do Chrome. O GridScout não preenche campos nem contorna verificações.</div>
              <button
                type="button"
                onClick={() => manualCompleteMutation.mutate()}
                disabled={manualCompleteMutation.isPending || workerUnavailable}
                className="w-full inline-flex items-center justify-center gap-2 rounded bg-accent px-3 py-2 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50"
              >
                <CheckCircle2 className="h-3.5 w-3.5" /> {manualCompleteMutation.isPending ? 'Confirmando sessão...' : 'Concluí o login no navegador'}
              </button>
              <button
                type="button"
                onClick={() => manualStartMutation.mutate()}
                disabled={manualStartMutation.isPending || manualCompleteMutation.isPending || workerUnavailable}
                className="w-full inline-flex items-center justify-center gap-2 rounded border border-sky-300 px-3 py-2 text-xs font-medium text-sky-900 hover:bg-sky-100 disabled:opacity-50"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${manualStartMutation.isPending ? 'animate-spin' : ''}`} /> Reabrir login manual
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => manualStartMutation.mutate()}
              disabled={manualStartMutation.isPending || workerUnavailable}
              className="w-full inline-flex items-center justify-center gap-2 rounded bg-slate-900 px-3 py-2 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50 shadow-sm"
            >
              <LogIn className="h-4 w-4" /> {manualStartMutation.isPending ? 'Abrindo navegador...' : 'Abrir login manual no navegador'}
            </button>
          )}
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => revalidateMutation.mutate()}
            disabled={revalidateMutation.isPending || logoutMutation.isPending}
            className="inline-flex items-center justify-center gap-2 border border-slate-300 text-slate-700 hover:bg-slate-50 rounded px-3 py-2 text-xs font-medium disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${revalidateMutation.isPending ? 'animate-spin' : ''}`} /> Verificar agora
          </button>
          <button
            type="button"
            onClick={switchAccount}
            disabled={logoutMutation.isPending}
            className="inline-flex items-center justify-center gap-2 border border-slate-300 text-slate-700 hover:bg-slate-50 rounded px-3 py-2 text-xs font-medium disabled:opacity-50 transition-colors"
          >
            <LogIn className="w-3.5 h-3.5" /> Trocar conta
          </button>
          <button
            type="button"
            onClick={() => logoutMutation.mutate()}
            disabled={logoutMutation.isPending}
            className="inline-flex items-center justify-center gap-2 border border-rose-200 text-rose-700 hover:bg-rose-50 rounded px-3 py-2 text-xs font-medium disabled:opacity-50 transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" /> Remover sessão
          </button>
          </div>
        )
      )}

      {authStatus && (
        <details className="group rounded-md border border-slate-200 bg-slate-50 text-xs">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 text-slate-700 font-medium">
            Detalhes da sessão
            <ChevronDown className="w-4 h-4 transition-transform group-open:rotate-180" />
          </summary>
          <dl className="border-t border-slate-200 divide-y divide-slate-200">
            <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Login registrado</dt><dd className="text-right text-slate-900">{formatDateTime(authStatus?.authenticated_at)}</dd></div>
            <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Última verificação</dt><dd className="text-right text-slate-900">{formatDateTime(authStatus?.last_checked_at)}</dd></div>
            <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Validade</dt><dd className="text-right text-slate-900">Controlada pela OLX</dd></div>
            <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500 flex items-center gap-1">Cookies em cache <FieldTip content="Diagnóstico local; cookies por si só não confirmam login." size={12} /></dt><dd className="text-right text-slate-900">{authStatus?.cookies_cached ?? 0}</dd></div>
            <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Browser-worker</dt><dd className="text-right text-slate-900">{authStatus?.worker_available ? 'Disponível' : 'Indisponível'}</dd></div>
            <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Chrome visível</dt><dd className="text-right text-slate-900">{authStatus?.browser_ready ? 'Disponível' : 'Será aberto quando necessário'}</dd></div>
            <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Escopo</dt><dd className="text-right text-slate-900">Busca e detalhe</dd></div>
            <div className="flex items-center gap-2 px-3 py-2 text-emerald-700"><ExternalLink className="w-3.5 h-3.5" /> Nenhuma ação de escrita habilitada</div>
            <div className="border-t border-slate-200 px-3 py-2 font-medium text-slate-700">Budget e cooldown</div>
            {rateLimit ? (
              <>
                <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Estado do rate-limit</dt><dd className={rateLimit.state === 'cooldown' ? 'text-right font-medium text-amber-800' : 'text-right font-medium text-slate-900'}>{rateLimit.state === 'cooldown' ? 'Cooldown ativo' : 'Fechado'}</dd></div>
                <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Budget horário</dt><dd className="text-right text-slate-900">{rateLimit.used} / {rateLimit.limit_per_hour} usados · {rateLimit.remaining} restantes</dd></div>
                <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Reset</dt><dd className="text-right text-slate-900">{formatDateTime(rateLimit.reset_at)}</dd></div>
                <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Próxima tentativa</dt><dd className="text-right text-slate-900">{rateLimit.retry_after_seconds ? `${rateLimit.retry_after_seconds}s` : 'Disponível'}</dd></div>
                <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Intervalo entre consultas</dt><dd className="text-right text-slate-900">{rateLimit.min_interval_seconds}–{rateLimit.max_interval_seconds}s</dd></div>
              </>
            ) : (
              <div className="px-3 py-2 text-slate-500">Limite de consultas indisponível.</div>
            )}
            <div className="flex justify-between gap-4 px-3 py-2"><dt className="text-slate-500">Etapa do login</dt><dd className="text-right text-slate-900">{authStatus.login_stage || 'não informada'}</dd></div>
            {authStatus?.error_code && <div className="px-3 py-2 text-rose-800"><span className="font-medium">Diagnóstico: </span>{authStatus.error_code}{authStatus.error_message ? ` — ${authStatus.error_message}` : ''}</div>}
          </dl>
        </details>
      )}
    </div>
  );
}
