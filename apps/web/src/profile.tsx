import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { useQuery, type QueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeftRight,
  Briefcase,
  Check,
  Compass,
  Cpu,
  Flame,
  Laptop,
  Pencil,
  Search,
  Shield,
  Sparkles,
  Target,
  TrendingUp,
  User,
  Zap,
} from 'lucide-react';
import {
  createProfile,
  fetcher,
  normalizeProfiles,
  profileLabel,
  setActiveProfile,
  updateProfile,
  type Profile,
} from './api';

const ACTIVE_PROFILE_STORAGE_KEY = 'gridscout.active-profile-id';

export const PROFILE_ICONS = [
  'user',
  'compass',
  'cpu',
  'zap',
  'shield',
  'flame',
  'target',
  'sparkles',
  'laptop',
  'search',
  'trending-up',
  'briefcase',
] as const;

export type ProfileIconKey = (typeof PROFILE_ICONS)[number];

export function renderProfileIcon(iconKey?: string, size = 18, className = '') {
  switch (iconKey) {
    case 'compass': return <Compass size={size} className={className} />;
    case 'cpu': return <Cpu size={size} className={className} />;
    case 'zap': return <Zap size={size} className={className} />;
    case 'shield': return <Shield size={size} className={className} />;
    case 'flame': return <Flame size={size} className={className} />;
    case 'target': return <Target size={size} className={className} />;
    case 'sparkles': return <Sparkles size={size} className={className} />;
    case 'laptop': return <Laptop size={size} className={className} />;
    case 'search': return <Search size={size} className={className} />;
    case 'trending-up': return <TrendingUp size={size} className={className} />;
    case 'briefcase': return <Briefcase size={size} className={className} />;
    case 'user':
    default:
      return <User size={size} className={className} />;
  }
}

export const PROFILE_COLORS = [
  { key: 'sky', bg: 'bg-sky-500/15', text: 'text-sky-400', border: 'border-sky-500/30', hex: '#38bdf8' },
  { key: 'emerald', bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30', hex: '#34d399' },
  { key: 'indigo', bg: 'bg-indigo-500/15', text: 'text-indigo-400', border: 'border-indigo-500/30', hex: '#818cf8' },
  { key: 'amber', bg: 'bg-amber-500/15', text: 'text-amber-400', border: 'border-amber-500/30', hex: '#fbbf24' },
  { key: 'rose', bg: 'bg-rose-500/15', text: 'text-rose-400', border: 'border-rose-500/30', hex: '#fb7185' },
  { key: 'violet', bg: 'bg-violet-500/15', text: 'text-violet-400', border: 'border-violet-500/30', hex: '#a78bfa' },
  { key: 'cyan', bg: 'bg-cyan-500/15', text: 'text-cyan-400', border: 'border-cyan-500/30', hex: '#22d3ee' },
  { key: 'orange', bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/30', hex: '#fb923c' },
] as const;

export function getProfileColor(colorKey?: string) {
  return PROFILE_COLORS.find(c => c.key === colorKey) || PROFILE_COLORS[0];
}

export function ProfileAvatar({
  icon = 'user',
  color = 'sky',
  size = 18,
  className = 'w-9 h-9 rounded-xl',
}: {
  icon?: string;
  color?: string;
  size?: number;
  className?: string;
}) {
  const c = getProfileColor(color);
  return (
    <div
      className={`inline-flex shrink-0 items-center justify-center border shadow-xs transition-transform ${c.bg} ${c.text} ${c.border} ${className}`}
    >
      {renderProfileIcon(icon, size)}
    </div>
  );
}

function storedProfileId(): string | null {
  try {
    return globalThis.localStorage?.getItem(ACTIVE_PROFILE_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

function rememberProfile(profile: Profile | null) {
  try {
    if (profile?.id) globalThis.localStorage?.setItem(ACTIVE_PROFILE_STORAGE_KEY, profile.id);
    else globalThis.localStorage?.removeItem(ACTIVE_PROFILE_STORAGE_KEY);
  } catch {
    // Browser storage convenience for local application
  }
}

export interface ProfileContextValue {
  profile: Profile | null;
  profiles: Profile[];
  profileId: string | undefined;
  isReady: boolean;
  isManaged: boolean;
  isSwitching: boolean;
  selectProfile: (profile: Profile) => void;
  exitToProfiles: () => void;
}

const unmanagedContext: ProfileContextValue = {
  profile: null,
  profiles: [],
  profileId: undefined,
  isReady: true,
  isManaged: false,
  isSwitching: false,
  selectProfile: () => undefined,
  exitToProfiles: () => undefined,
};

const ProfileContext = createContext<ProfileContextValue>(unmanagedContext);

export function useProfile(): ProfileContextValue {
  return useContext(ProfileContext);
}

function bootstrapErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Não foi possível carregar os perfis.';
}

export function ProfileGate({ children, queryClient }: { children: React.ReactNode; queryClient: QueryClient }) {
  const navigate = useNavigate();
  const profilesQuery = useQuery<unknown>({
    queryKey: ['profiles'],
    queryFn: () => fetcher<unknown>('/profiles'),
    staleTime: Infinity,
    retry: false,
  });
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [isSwitching, setIsSwitching] = useState(false);

  // Form states (for creation and alteration)
  const [editingProfile, setEditingProfile] = useState<Profile | null>(null);
  const [formName, setFormName] = useState('');
  const [selectedIcon, setSelectedIcon] = useState<string>('user');
  const [selectedColor, setSelectedColor] = useState<string>('sky');
  const [isSaving, setIsSaving] = useState(false);

  const chooseInitialProfile = useCallback((selected: Profile) => {
    setActiveProfile(selected);
    rememberProfile(selected);
    setProfile(selected);
    setIsBootstrapping(false);
  }, []);

  useEffect(() => {
    setActiveProfile(null);
  }, []);

  useEffect(() => {
    if (!profilesQuery.isSuccess) return;
    const listed = normalizeProfiles(profilesQuery.data);
    setProfiles(listed);
    const restored = listed.find(item => item.id === storedProfileId());
    if (restored) {
      chooseInitialProfile(restored);
      return;
    }
    rememberProfile(null);
    setIsBootstrapping(false);
    setBootstrapError(null);
  }, [chooseInitialProfile, profilesQuery.data, profilesQuery.isSuccess]);

  const startEditing = useCallback((target: Profile) => {
    setEditingProfile(target);
    setFormName(target.name || target.display_name || '');
    setSelectedIcon(target.preferences?.icon || 'user');
    setSelectedColor(target.preferences?.color || 'sky');
    setBootstrapError(null);
  }, []);

  const cancelEditing = useCallback(() => {
    setEditingProfile(null);
    setFormName('');
    setSelectedIcon('user');
    setSelectedColor('sky');
    setBootstrapError(null);
  }, []);

  const handleSaveProfile = useCallback(async () => {
    const name = formName.trim();
    if (!name || isSaving) return;
    setIsSaving(true);
    setBootstrapError(null);
    try {
      if (editingProfile) {
        const updated = await updateProfile(editingProfile.id, {
          name,
          display_name: name,
          preferences: { icon: selectedIcon, color: selectedColor },
        });
        const nextProfiles = profiles.map(item => (item.id === updated.id ? updated : item));
        setProfiles(nextProfiles);
        cancelEditing();
      } else {
        const created = await createProfile({
          name,
          display_name: name,
          preferences: { icon: selectedIcon, color: selectedColor },
        });
        const nextProfiles = [...profiles.filter(item => item.id !== created.id), created];
        setFormName('');
        setSelectedIcon('user');
        setSelectedColor('sky');
        setProfiles(nextProfiles);
        chooseInitialProfile(created);
      }
    } catch {
      setBootstrapError(editingProfile ? 'Não foi possível atualizar este perfil.' : 'Não foi possível criar este perfil.');
    } finally {
      setIsSaving(false);
    }
  }, [cancelEditing, chooseInitialProfile, editingProfile, formName, isSaving, profiles, selectedColor, selectedIcon]);

  const selectProfile = useCallback((next: Profile) => {
    if (!next.id || next.id === profile?.id || isSwitching) return;
    setIsSwitching(true);
    setProfile(null);
    setActiveProfile(null);
    queryClient.clear();
    navigate('/');
    setActiveProfile(next);
    rememberProfile(next);
    setProfile(next);
    setIsSwitching(false);
  }, [isSwitching, navigate, profile?.id, queryClient]);

  const exitToProfiles = useCallback(() => {
    setIsSwitching(true);
    setProfile(null);
    setActiveProfile(null);
    rememberProfile(null);
    queryClient.clear();
    navigate('/');
    setIsSwitching(false);
  }, [navigate, queryClient]);

  const context: ProfileContextValue = {
    profile,
    profiles,
    profileId: profile?.id,
    isReady: Boolean(profile) && !isBootstrapping && !isSwitching,
    isManaged: true,
    isSwitching,
    selectProfile,
    exitToProfiles,
  };

  if (profilesQuery.isError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6 text-slate-900">
        <section className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold">Perfil indisponível</h1>
          <p className="mt-2 text-sm text-slate-600">{bootstrapErrorMessage(profilesQuery.error)}</p>
          <button type="button" onClick={() => { setBootstrapError(null); setIsBootstrapping(true); void profilesQuery.refetch(); }} className="mt-5 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white">Tentar novamente</button>
        </section>
      </div>
    );
  }

  if (!context.isReady) {
    if (!isBootstrapping && !profile) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6 text-slate-900">
          <section aria-labelledby="profile-gate-title" className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h1 id="profile-gate-title" className="text-xl font-semibold">Perfis de Pesquisa</h1>
            <p className="mt-1 text-sm text-slate-600">Selecione o perfil de operação ou crie um novo para iniciar as buscas.</p>

            {profiles.length > 0 && (
              <div className="mt-5 grid gap-2 sm:grid-cols-2">
                {profiles.map(item => {
                  const itemIcon = item.preferences?.icon || 'user';
                  const itemColor = item.preferences?.color || 'sky';
                  const isBeingEdited = editingProfile?.id === item.id;
                  return (
                    <div
                      key={item.id}
                      className={`group flex items-center justify-between rounded-xl border p-2.5 transition-all ${
                        isBeingEdited
                          ? 'border-slate-900 bg-slate-100/80 ring-1 ring-slate-900'
                          : 'border-slate-200 bg-white hover:border-slate-900 hover:shadow-xs'
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => chooseInitialProfile(item)}
                        className="flex flex-1 items-center gap-2.5 text-left min-w-0 pr-2"
                      >
                        <ProfileAvatar
                          icon={itemIcon}
                          color={itemColor}
                          size={18}
                          className="w-9 h-9 rounded-lg"
                        />
                        <span className="truncate font-medium text-slate-900 group-hover:text-black">
                          {profileLabel(item)}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => startEditing(item)}
                        aria-label="Editar perfil"
                        title="Editar perfil"
                        className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-800"
                      >
                        <Pencil size={14} />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            <form
              className={`mt-6 ${profiles.length > 0 ? 'border-t border-slate-100 pt-5' : ''}`}
              onSubmit={event => {
                event.preventDefault();
                void handleSaveProfile();
              }}
            >
              <div className="flex items-center justify-between">
                <label htmlFor="new-profile-name" className="text-sm font-medium text-slate-700">
                  {editingProfile ? 'Alterar perfil' : 'Criar novo perfil'}
                </label>
                {editingProfile && (
                  <button
                    type="button"
                    onClick={cancelEditing}
                    className="text-xs text-slate-500 hover:text-slate-800 transition-colors"
                  >
                    Cancelar
                  </button>
                )}
              </div>

              <div className="mt-2 flex items-center gap-3">
                <ProfileAvatar
                  icon={selectedIcon}
                  color={selectedColor}
                  size={22}
                  className="w-11 h-11 rounded-xl"
                />
                <input
                  id="new-profile-name"
                  value={formName}
                  onChange={event => setFormName(event.target.value)}
                  placeholder="Nome do perfil"
                  className="flex-1 rounded-md border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-900"
                />
              </div>

              {/* Seletor de Ícones (intuitivo, sem texto) */}
              <div className="mt-3.5">
                <div className="grid grid-cols-6 gap-1.5 sm:grid-cols-12">
                  {PROFILE_ICONS.map(iconKey => {
                    const isSelected = selectedIcon === iconKey;
                    return (
                      <button
                        key={iconKey}
                        type="button"
                        onClick={() => setSelectedIcon(iconKey)}
                        aria-label={`Ícone ${iconKey}`}
                        className={`flex h-8 items-center justify-center rounded-lg border transition-all ${
                          isSelected
                            ? 'border-slate-900 bg-slate-900 text-white shadow-xs'
                            : 'border-slate-200 bg-slate-50 text-slate-600 hover:border-slate-400 hover:bg-white'
                        }`}
                      >
                        {renderProfileIcon(iconKey, 15)}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Seletor de Cores (paleta visual, sem texto) */}
              <div className="mt-3">
                <div className="flex flex-wrap items-center gap-2">
                  {PROFILE_COLORS.map(c => {
                    const isSelected = selectedColor === c.key;
                    return (
                      <button
                        key={c.key}
                        type="button"
                        onClick={() => setSelectedColor(c.key)}
                        aria-label={`Cor ${c.key}`}
                        style={{ backgroundColor: c.hex }}
                        className={`flex h-6 w-6 items-center justify-center rounded-full transition-transform ${
                          isSelected ? 'scale-115 ring-2 ring-slate-900 ring-offset-2' : 'hover:scale-105 opacity-85 hover:opacity-100'
                        }`}
                      >
                        {isSelected && <Check size={12} className="text-white drop-shadow-xs" />}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="mt-4 flex items-center gap-2">
                <button
                  type="submit"
                  disabled={!formName.trim() || isSaving}
                  className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-50"
                >
                  {isSaving ? 'Salvando…' : editingProfile ? 'Salvar alterações' : 'Criar e continuar'}
                </button>
                {editingProfile && (
                  <button
                    type="button"
                    onClick={cancelEditing}
                    className="rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
                  >
                    Cancelar
                  </button>
                )}
              </div>
            </form>

            {bootstrapError && <p role="alert" className="mt-3 text-sm text-rose-600">{bootstrapError}</p>}
          </section>
        </div>
      );
    }
    return (
      <div role="status" aria-live="polite" className="flex min-h-screen items-center justify-center bg-slate-50 p-6 text-sm text-slate-500">
        Preparando seu perfil…
      </div>
    );
  }

  return <ProfileContext.Provider value={context}>{children}</ProfileContext.Provider>;
}

export function ProfileSwitcher({ compact = false }: { compact?: boolean }) {
  const { profile, profiles, selectProfile, exitToProfiles, isSwitching } = useProfile();
  if (!profile) return null;

  const icon = profile.preferences?.icon || 'user';
  const color = profile.preferences?.color || 'sky';

  if (compact) {
    return (
      <div className="flex justify-center border-b border-slate-800 p-2 pb-3">
        <button
          type="button"
          onClick={exitToProfiles}
          disabled={isSwitching}
          title={`Perfil: ${profileLabel(profile)} · Trocar perfil`}
          aria-label="Trocar perfil"
          className="group relative flex h-10 w-10 items-center justify-center rounded-xl transition-all hover:scale-105"
        >
          <ProfileAvatar icon={icon} color={color} size={18} className="h-10 w-10 rounded-xl" />
          <div className="absolute -bottom-1 -right-1 rounded-full border border-slate-700 bg-slate-900 p-0.5 text-slate-400 shadow-xs group-hover:text-white">
            <ArrowLeftRight size={10} />
          </div>
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center border-b border-slate-800 px-3 py-3.5 text-center">
      {/* Hidden select for accessibility and automated test compatibility */}
      <select
        aria-label="Perfil ativo"
        value={profile.id}
        disabled={isSwitching || profiles.length < 2}
        onChange={event => {
          const next = profiles.find(item => item.id === event.target.value);
          if (next) selectProfile(next);
        }}
        className="sr-only"
      >
        {profiles.map(item => (
          <option key={item.id} value={item.id}>
            {profileLabel(item)}
          </option>
        ))}
      </select>

      {/* Perfil centralizado no navegador com ícone e nome */}
      <div className="flex w-full flex-col items-center justify-center">
        <ProfileAvatar
          icon={icon}
          color={color}
          size={22}
          className="mb-2 h-12 w-12 rounded-2xl"
        />
        <div className="flex max-w-full items-center justify-center gap-1.5 px-2">
          <span className="truncate text-xs font-semibold text-slate-200" title={profileLabel(profile)}>
            {profileLabel(profile)}
          </span>
          <button
            type="button"
            onClick={exitToProfiles}
            disabled={isSwitching}
            aria-label="Trocar perfil"
            title="Trocar perfil"
            className="shrink-0 rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-800 hover:text-sky-400 disabled:opacity-50"
          >
            <ArrowLeftRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
