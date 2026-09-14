export type CategoryType = 'deterministic' | 'mocked' | 'all';

export interface MarketCategory {
  id: string;
  name: string;
  shortName: string;
  iconName: string;
  type: CategoryType;
  description: string;
  productCount: number;
  tip: string;
}

export interface MockMarketProduct {
  id: string;
  categoryId: string;
  display_name: string;
  category: string;
  active_listings_count: number;
  type: 'deterministic' | 'mocked';
  marketData: {
    product_name: string;
    heat_band: 'HOT' | 'WARM' | 'COLD';
    market_heat: number;
    sample_size: number;
    confidence: number;
    asking_median: number;
    estimated_clearing_value: number;
    fast_sale_value: number;
    p10: number;
    p25: number;
    median: number;
    p75: number;
    p90: number;
    mad: number;
  };
}

export const MARKET_CATEGORIES: MarketCategory[] = [
  {
    id: 'all',
    name: 'Todas as Categorias',
    shortName: 'Todas',
    iconName: 'LayoutGrid',
    type: 'all',
    description: 'Visualização consolidada de todos os segmentos e categorias monitorados.',
    productCount: 12,
    tip: 'Exibe todos os produtos monitorados pelo GridScout em todos os segmentos.',
  },
  {
    id: 'informatica',
    name: 'Informática & Hardware',
    shortName: 'Informática',
    iconName: 'Laptop',
    type: 'deterministic',
    description: 'Placas de vídeo, processadores e hardware com regras determinísticas e catálogo estruturado.',
    productCount: 3,
    tip: 'Categoria Determinística: precificação calculada diretamente pelo motor de regras determinísticas da plataforma.',
  },
  {
    id: 'telefonia',
    name: 'Telefones & Celulares',
    shortName: 'Telefonia',
    iconName: 'Smartphone',
    type: 'mocked',
    description: 'Smartphones premium, dobráveis e aparelhos móveis de alta rotatividade.',
    productCount: 3,
    tip: 'Categoria Mockada: dados simulados para expansão de catálogo e testes de interface de mercado.',
  },
  {
    id: 'roupas',
    name: 'Roupas & Acessórios',
    shortName: 'Roupas & Acessórios',
    iconName: 'Shirt',
    type: 'mocked',
    description: 'Sneakers colecionáveis, relógios de luxo, vestuário streetwear e acessórios.',
    productCount: 3,
    tip: 'Categoria Mockada: simulação de precificação para itens de moda, sneakers e relógios.',
  },
  {
    id: 'games',
    name: 'Games & Consoles',
    shortName: 'Games & Consoles',
    iconName: 'Gamepad2',
    type: 'mocked',
    description: 'Consoles de última geração, portáteis, controles e jogos colecionáveis.',
    productCount: 2,
    tip: 'Categoria Mockada: simulação de liquidez e valores de mercado para o segmento gamer.',
  },
  {
    id: 'audio',
    name: 'Áudio & Som',
    shortName: 'Áudio',
    iconName: 'Headphones',
    type: 'mocked',
    description: 'Fones com cancelamento de ruído, caixas bluetooth e equipamentos de áudio.',
    productCount: 1,
    tip: 'Categoria Mockada: dados projetados para headphones e caixas de som.',
  },
  {
    id: 'fotografia',
    name: 'Fotografia & Câmeras',
    shortName: 'Fotografia',
    iconName: 'Camera',
    type: 'mocked',
    description: 'Câmeras mirrorless profissionais, lentes e equipamentos audiovisuais.',
    productCount: 1,
    tip: 'Categoria Mockada: simulação de valores para equipamentos fotográficos.',
  },
];

export const MOCK_MARKET_PRODUCTS: MockMarketProduct[] = [
  // Informática (Deterministic)
  {
    id: 'prod-gpu-rtx3080',
    categoryId: 'informatica',
    display_name: 'NVIDIA GeForce RTX 3080 10GB',
    category: 'gpu',
    active_listings_count: 14,
    type: 'deterministic',
    marketData: {
      product_name: 'NVIDIA GeForce RTX 3080 10GB',
      heat_band: 'HOT',
      market_heat: 84.5,
      sample_size: 28,
      confidence: 0.94,
      asking_median: 2850.0,
      estimated_clearing_value: 2700.0,
      fast_sale_value: 2350.0,
      p10: 2200.0,
      p25: 2550.0,
      median: 2850.0,
      p75: 3100.0,
      p90: 3400.0,
      mad: 280.0,
    },
  },
  {
    id: 'prod-nb-macbookm2',
    categoryId: 'informatica',
    display_name: 'Apple MacBook Pro M2 16GB 512GB',
    category: 'notebook',
    active_listings_count: 8,
    type: 'deterministic',
    marketData: {
      product_name: 'Apple MacBook Pro M2 16GB 512GB',
      heat_band: 'HOT',
      market_heat: 89.0,
      sample_size: 19,
      confidence: 0.92,
      asking_median: 7800.0,
      estimated_clearing_value: 7350.0,
      fast_sale_value: 6800.0,
      p10: 6200.0,
      p25: 6900.0,
      median: 7800.0,
      p75: 8400.0,
      p90: 8900.0,
      mad: 650.0,
    },
  },
  {
    id: 'prod-cpu-ryzen7',
    categoryId: 'informatica',
    display_name: 'AMD Ryzen 7 5800X3D AM4',
    category: 'cpu',
    active_listings_count: 6,
    type: 'deterministic',
    marketData: {
      product_name: 'AMD Ryzen 7 5800X3D AM4',
      heat_band: 'WARM',
      market_heat: 72.3,
      sample_size: 15,
      confidence: 0.88,
      asking_median: 1650.0,
      estimated_clearing_value: 1520.0,
      fast_sale_value: 1380.0,
      p10: 1250.0,
      p25: 1420.0,
      median: 1650.0,
      p75: 1800.0,
      p90: 1950.0,
      mad: 160.0,
    },
  },

  // Telefonia (Mocked)
  {
    id: 'mock-phone-iphone14pro',
    categoryId: 'telefonia',
    display_name: 'Apple iPhone 14 Pro 128GB Grafite',
    category: 'smartphone',
    active_listings_count: 22,
    type: 'mocked',
    marketData: {
      product_name: 'Apple iPhone 14 Pro 128GB Grafite',
      heat_band: 'HOT',
      market_heat: 91.2,
      sample_size: 45,
      confidence: 0.95,
      asking_median: 4500.0,
      estimated_clearing_value: 4200.0,
      fast_sale_value: 3850.0,
      p10: 3600.0,
      p25: 3950.0,
      median: 4500.0,
      p75: 4800.0,
      p90: 5100.0,
      mad: 350.0,
    },
  },
  {
    id: 'mock-phone-s23ultra',
    categoryId: 'telefonia',
    display_name: 'Samsung Galaxy S23 Ultra 256GB Phantom Black',
    category: 'smartphone',
    active_listings_count: 17,
    type: 'mocked',
    marketData: {
      product_name: 'Samsung Galaxy S23 Ultra 256GB Phantom Black',
      heat_band: 'WARM',
      market_heat: 78.4,
      sample_size: 32,
      confidence: 0.89,
      asking_median: 4100.0,
      estimated_clearing_value: 3800.0,
      fast_sale_value: 3450.0,
      p10: 3200.0,
      p25: 3600.0,
      median: 4100.0,
      p75: 4400.0,
      p90: 4700.0,
      mad: 320.0,
    },
  },
  {
    id: 'mock-phone-pixel7pro',
    categoryId: 'telefonia',
    display_name: 'Google Pixel 7 Pro 128GB Hazel',
    category: 'smartphone',
    active_listings_count: 9,
    type: 'mocked',
    marketData: {
      product_name: 'Google Pixel 7 Pro 128GB Hazel',
      heat_band: 'COLD',
      market_heat: 62.0,
      sample_size: 14,
      confidence: 0.82,
      asking_median: 2900.0,
      estimated_clearing_value: 2650.0,
      fast_sale_value: 2350.0,
      p10: 2150.0,
      p25: 2450.0,
      median: 2900.0,
      p75: 3200.0,
      p90: 3500.0,
      mad: 280.0,
    },
  },

  // Roupas & Acessórios (Mocked)
  {
    id: 'mock-clothing-jordan1',
    categoryId: 'roupas',
    display_name: 'Tênis Nike Air Jordan 1 Retro High Chicago',
    category: 'roupas',
    active_listings_count: 18,
    type: 'mocked',
    marketData: {
      product_name: 'Tênis Nike Air Jordan 1 Retro High Chicago',
      heat_band: 'HOT',
      market_heat: 87.5,
      sample_size: 34,
      confidence: 0.91,
      asking_median: 1850.0,
      estimated_clearing_value: 1700.0,
      fast_sale_value: 1500.0,
      p10: 1350.0,
      p25: 1550.0,
      median: 1850.0,
      p75: 2100.0,
      p90: 2400.0,
      mad: 210.0,
    },
  },
  {
    id: 'mock-clothing-rolexsub',
    categoryId: 'roupas',
    display_name: 'Relógio Rolex Submariner Date 41mm Oystersteel',
    category: 'roupas',
    active_listings_count: 5,
    type: 'mocked',
    marketData: {
      product_name: 'Relógio Rolex Submariner Date 41mm Oystersteel',
      heat_band: 'WARM',
      market_heat: 76.0,
      sample_size: 11,
      confidence: 0.88,
      asking_median: 68000.0,
      estimated_clearing_value: 64500.0,
      fast_sale_value: 60000.0,
      p10: 57000.0,
      p25: 61500.0,
      median: 68000.0,
      p75: 72000.0,
      p90: 76500.0,
      mad: 4200.0,
    },
  },
  {
    id: 'mock-clothing-rayban',
    categoryId: 'roupas',
    display_name: 'Óculos de Sol Ray-Ban Wayfarer Classic Polarizado',
    category: 'roupas',
    active_listings_count: 16,
    type: 'mocked',
    marketData: {
      product_name: 'Óculos de Sol Ray-Ban Wayfarer Classic Polarizado',
      heat_band: 'HOT',
      market_heat: 82.3,
      sample_size: 29,
      confidence: 0.93,
      asking_median: 650.0,
      estimated_clearing_value: 580.0,
      fast_sale_value: 500.0,
      p10: 450.0,
      p25: 520.0,
      median: 650.0,
      p75: 720.0,
      p90: 790.0,
      mad: 75.0,
    },
  },

  // Games & Consoles (Mocked)
  {
    id: 'mock-game-ps5',
    categoryId: 'games',
    display_name: 'PlayStation 5 Slim 1TB Edição Digital',
    category: 'games',
    active_listings_count: 24,
    type: 'mocked',
    marketData: {
      product_name: 'PlayStation 5 Slim 1TB Edição Digital',
      heat_band: 'HOT',
      market_heat: 94.0,
      sample_size: 52,
      confidence: 0.96,
      asking_median: 3200.0,
      estimated_clearing_value: 2950.0,
      fast_sale_value: 2700.0,
      p10: 2500.0,
      p25: 2800.0,
      median: 3200.0,
      p75: 3400.0,
      p90: 3650.0,
      mad: 220.0,
    },
  },
  {
    id: 'mock-game-switcholed',
    categoryId: 'games',
    display_name: 'Nintendo Switch OLED 64GB Neon',
    category: 'games',
    active_listings_count: 15,
    type: 'mocked',
    marketData: {
      product_name: 'Nintendo Switch OLED 64GB Neon',
      heat_band: 'HOT',
      market_heat: 83.2,
      sample_size: 31,
      confidence: 0.92,
      asking_median: 2100.0,
      estimated_clearing_value: 1950.0,
      fast_sale_value: 1750.0,
      p10: 1600.0,
      p25: 1800.0,
      median: 2100.0,
      p75: 2300.0,
      p90: 2500.0,
      mad: 180.0,
    },
  },

  // Áudio & Som (Mocked)
  {
    id: 'mock-audio-sonywh1000xm5',
    categoryId: 'audio',
    display_name: 'Fone de Ouvido Sony WH-1000XM5 ANC',
    category: 'audio',
    active_listings_count: 11,
    type: 'mocked',
    marketData: {
      product_name: 'Fone de Ouvido Sony WH-1000XM5 ANC',
      heat_band: 'HOT',
      market_heat: 86.4,
      sample_size: 23,
      confidence: 0.9,
      asking_median: 1950.0,
      estimated_clearing_value: 1800.0,
      fast_sale_value: 1600.0,
      p10: 1450.0,
      p25: 1650.0,
      median: 1950.0,
      p75: 2150.0,
      p90: 2350.0,
      mad: 190.0,
    },
  },

  // Fotografia & Câmeras (Mocked)
  {
    id: 'mock-photo-sonyalpha7iv',
    categoryId: 'fotografia',
    display_name: 'Câmera Mirrorless Sony Alpha A7 IV (Corpo)',
    category: 'fotografia',
    active_listings_count: 7,
    type: 'mocked',
    marketData: {
      product_name: 'Câmera Mirrorless Sony Alpha A7 IV (Corpo)',
      heat_band: 'WARM',
      market_heat: 74.5,
      sample_size: 16,
      confidence: 0.87,
      asking_median: 14200.0,
      estimated_clearing_value: 13500.0,
      fast_sale_value: 12200.0,
      p10: 11000.0,
      p25: 12500.0,
      median: 14200.0,
      p75: 15500.0,
      p90: 16800.0,
      mad: 1150.0,
    },
  },
];
