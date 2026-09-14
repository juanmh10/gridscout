import { useQuery } from '@tanstack/react-query';
import {
  Cpu,
  HardDrive,
  LayoutGrid,
  Laptop,
  MemoryStick,
  MonitorCog,
  Package,
  RectangleHorizontal,
} from 'lucide-react';
import { fetcher } from '../api';
import { useProfile } from '../profile';

export interface ProductCategory {
  id: string;
  name: string;
  product_count: number;
  active_product_count?: number;
  listing_count?: number;
}

interface CategoryStripProps {
  selectedCategoryId: string;
  onSelectCategory: (categoryId: string) => void;
  orientation?: 'horizontal' | 'vertical';
  showAllOption?: boolean;
}

const ICONS: Record<string, React.ElementType> = {
  gpu: MonitorCog,
  cpu: Cpu,
  notebook: Laptop,
  ram: MemoryStick,
  ssd: HardDrive,
  motherboard: RectangleHorizontal,
  other: Package,
};

function CategoryIcon({ category }: { category: ProductCategory }) {
  const Icon = ICONS[category.id] || Package;
  return <Icon aria-hidden="true" size={16} strokeWidth={1.8} />;
}

export function CategoryStrip({
  selectedCategoryId,
  onSelectCategory,
  orientation = 'horizontal',
  showAllOption = true,
}: CategoryStripProps) {
  const { profileId, isManaged } = useProfile();
  const { data, isLoading } = useQuery<{ items: ProductCategory[] }>({
    queryKey: ['product-categories', profileId],
    queryFn: () => fetcher<{ items: ProductCategory[] }>('/products/categories'),
    staleTime: 60_000,
    enabled: !isManaged || Boolean(profileId),
  });

  const categories: ProductCategory[] = [
    ...(showAllOption
      ? [
          {
            id: 'all',
            name: 'Todas as categorias',
            product_count: data?.items?.reduce((total, item) => total + (item.product_count || 0), 0) || 0,
          },
        ]
      : []),
    ...(data?.items || []),
  ];

  if (orientation === 'vertical') {
    return (
      <div className="flex flex-col space-y-1" role="tablist" aria-label="Categorias de hardware">
        <div className="px-2 py-1 text-[11px] font-semibold tracking-wider text-slate-400 uppercase">
          Categorias
        </div>
        {categories.map((category) => {
          const isSelected = selectedCategoryId === category.id;
          return (
            <button
              key={category.id}
              type="button"
              role="tab"
              aria-selected={isSelected}
              disabled={isLoading && category.id !== 'all'}
              onClick={() => onSelectCategory(category.id)}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                isSelected
                  ? 'bg-slate-900 text-white shadow-sm'
                  : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-2.5">
                {category.id === 'all' ? (
                  <LayoutGrid aria-hidden="true" size={15} strokeWidth={1.8} />
                ) : (
                  <CategoryIcon category={category} />
                )}
                <span>{category.name}</span>
              </div>
              {category.product_count > 0 && (
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                    isSelected ? 'bg-slate-700 text-slate-100' : 'bg-slate-100 text-slate-600'
                  }`}
                >
                  {category.product_count}
                </span>
              )}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div
      className="flex min-h-12 items-center gap-1.5 overflow-x-auto rounded-lg border border-slate-200 bg-white p-1.5 shadow-sm"
      role="tablist"
      aria-label="Categorias de hardware"
    >
      {categories.map((category) => {
        const isSelected = selectedCategoryId === category.id;
        return (
          <button
            key={category.id}
            type="button"
            role="tab"
            aria-selected={isSelected}
            disabled={isLoading && category.id !== 'all'}
            onClick={() => onSelectCategory(category.id)}
            className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 shrink-0 ${
              isSelected
                ? 'bg-slate-900 text-white shadow-sm'
                : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
            }`}
          >
            {category.id === 'all' ? <LayoutGrid aria-hidden="true" size={15} strokeWidth={1.8} /> : <CategoryIcon category={category} />}
            <span>{category.name}</span>
            {category.product_count > 0 && (
              <span
                className={`rounded-full px-1.5 py-0.2 text-[10px] font-semibold ${
                  isSelected ? 'bg-slate-700 text-slate-100' : 'bg-slate-100 text-slate-600'
                }`}
              >
                {category.product_count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export default CategoryStrip;
