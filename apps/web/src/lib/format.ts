export function formatBRL(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return 'R$ 0,00';
  const num = typeof value === 'string' ? parseFloat(value) : value;
  if (isNaN(num)) return 'R$ 0,00';
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
    .format(num)
    .replace(/\u00a0/g, ' ');
}

export function formatPercent(value: number | null | undefined, decimals: number = 1): string {
  if (value === null || value === undefined) return '0,0%';
  const val = value * 100;
  return `${val.toLocaleString('pt-BR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}%`;
}

export function formatNumber(value: number | null | undefined, decimals: number = 1): string {
  if (value === null || value === undefined) return '0';
  return value.toLocaleString('pt-BR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '-';
  const raw = value instanceof Date ? value.toISOString() : String(value).trim();
  const dateStr = raw.endsWith('Z') || raw.includes('+') || (raw.includes('-') && raw.lastIndexOf('-') > 10) ? raw : `${raw}Z`;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return '-';
  return d.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' });
}

export function formatRecency(value: string | Date | null | undefined): string {
  if (!value) return '—';
  try {
    const raw = value instanceof Date ? value.toISOString() : String(value).trim();
    const dateStr = raw.endsWith('Z') || raw.includes('+') || (raw.includes('-') && raw.lastIndexOf('-') > 10) ? raw : `${raw}Z`;
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return '—';
    const now = new Date();

    const brFormatter = new Intl.DateTimeFormat('pt-BR', {
      timeZone: 'America/Sao_Paulo',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });

    const parts = brFormatter.formatToParts(d);
    const pMap: Record<string, string> = {};
    for (const p of parts) pMap[p.type] = p.value;

    const nowParts = brFormatter.formatToParts(now);
    const nowMap: Record<string, string> = {};
    for (const p of nowParts) nowMap[p.type] = p.value;

    const dateDayStr = `${pMap.year}-${pMap.month}-${pMap.day}`;
    const nowDayStr = `${nowMap.year}-${nowMap.month}-${nowMap.day}`;

    const yestDate = new Date(now.getTime() - 24 * 3600 * 1000);
    const yestParts = brFormatter.formatToParts(yestDate);
    const yestMap: Record<string, string> = {};
    for (const p of yestParts) yestMap[p.type] = p.value;
    const yestDayStr = `${yestMap.year}-${yestMap.month}-${yestMap.day}`;

    const hours = pMap.hour || '00';
    const minutes = pMap.minute || '00';
    const day = pMap.day || '01';
    const month = pMap.month || '01';
    const year = pMap.year || '2026';

    if (dateDayStr === nowDayStr) {
      return `Hoje, ${hours}:${minutes}`;
    }
    if (dateDayStr === yestDayStr) {
      return `Ontem, ${hours}:${minutes}`;
    }
    if (pMap.year === nowMap.year) {
      return `${day}/${month} ${hours}:${minutes}`;
    }
    return `${day}/${month}/${year}`;
  } catch {
    return '—';
  }
}

export function cleanLocation(loc: string | null | undefined): string {
  if (!loc) return 'Brasil';
  const cleaned = loc.replace(/\s*(?:hoje|ontem|\d{1,2}\s+de\s+[a-z]+|\d{1,2}\/\d{1,2}).*$/i, '').trim();
  return cleaned || 'Brasil';
}

export function translateStatus(status: string | null | undefined): string {
  if (!status) return '-';
  const lower = status.toLowerCase();
  switch (lower) {
    case 'completed':
      return 'Concluído';
    case 'completed_partial':
      return 'Concluído parcialmente';
    case 'running':
      return 'Executando';
    case 'reviewing_cards':
      return 'Revisando cards';
    case 'failed':
      return 'Falhou';
    case 'pending':
      return 'Pendente';
    case 'active':
      return 'Ativo';
    case 'inactive':
      return 'Inativo';
    case 'closed':
      return 'Encerrado';
    case 'idle':
      return 'Ocioso';
    case 'blocked':
      return 'Bloqueado';
    case 'queued':
      return 'Enfileirado';
    case 'cooldown':
      return 'Cooldown';
    case 'fallback':
      return 'Fallback';
    case 'cancelled':
    case 'canceled':
      return 'Cancelado';
    case 'interrupted':
      return 'Interrompido';
    default:
      return status;
  }
}

export function translateCondition(condition: string | null | undefined): string {
  if (!condition) return '-';
  const lower = condition.toLowerCase().replace(/\s+/g, '_');
  switch (lower) {
    case 'new':
      return 'Novo';
    case 'like_new':
      return 'Como novo';
    case 'good':
      return 'Bom estado';
    case 'fair':
      return 'Regular';
    case 'for_parts':
      return 'Para peças';
    default:
      return condition.replace(/_/g, ' ');
  }
}

export function translateCategory(category: string | null | undefined): string {
  if (!category || category === 'all') return 'Todas as categorias';
  const lower = category.toLowerCase();
  switch (lower) {
    case 'gpu':
      return 'Placa de Vídeo (GPU)';
    case 'notebook':
      return 'Notebook';
    case 'motherboard':
      return 'Placa-Mãe';
    case 'cpu':
      return 'Processador (CPU)';
    case 'ram':
      return 'Memória RAM';
    case 'ssd':
      return 'Armazenamento (SSD)';
    case 'complete_pc':
    case 'desktop':
      return 'Desktop Completo';
    case 'console':
      return 'Console';
    case 'peripheral':
      return 'Periférico';
    case 'smartphone':
      return 'Smartphone';
    case 'monitor':
      return 'Monitor';
    case 'other':
      return 'Outro';
    default:
      return category;
  }
}

export function canonicalSourceUrl(url: string | null | undefined): string {
  if (!url) return '';
  const trimmed = url.trim();
  if (trimmed.includes('olx.com.br')) {
    const match = trimmed.match(/(?:-|(?:\/vi\/|\/anuncio\/|id=))(\d{7,12})(?:\?|#|$|\/)/);
    if (match && match[1]) {
      return `https://www.olx.com.br/vi/${match[1]}`;
    }
  }
  return trimmed;
}

