// Synthetic data only. Shared fixtures for the real-browser PDF smoke test.
const product = {
  key: "code:1",
  code: "1",
  article: "A1",
  name: "Товар1",
  units: 5,
  revenue: 100,
  checks: 1,
  clients: 1,
  average_price: 20,
  discount_amount: 0,
  average_discount: 0,
  last_sale: "2026-01-10",
  revenue_share: 100,
};
const category = {
  category: "Категория1",
  current_revenue: 100,
  previous_revenue: 50,
  current_units: 5,
  current_checks: 1,
  current_average_check: 100,
  current_share: 100,
  previous_share: 100,
  share_change_pp: 0,
  revenue_change: 50,
  revenue_change_percent: 100,
  contribution_percent: 100,
};
const client = {
  client: "Клиент1",
  client_key: "client1",
  segment: "Новые",
  tags: [],
  r_score: 5,
  f_score: 3,
  m_score: 4,
  rfm_code: "534",
  last_purchase: "2026-01-10",
  first_purchase: "2026-01-01",
  recency_days: 1,
  frequency: 2,
  purchases_per_month: 2,
  average_interval: 1,
  cycle_overdue: 0,
  monetary: 100,
  average_check: 50,
  units: 5,
  unique_products: 1,
  departments_count: 1,
};
const period = { start: "2026-01-01", end: "2026-01-10" };
const metrics = Object.fromEntries(
  [
    "revenue",
    "units",
    "checks",
    "average_check",
    "categories",
    "sales_count",
    "items_count",
    "average_items_per_sale",
    "items",
    "items_per_check",
    "average_discount",
  ].map((k) => [
    k,
    { current: 100, previous: 50, difference: 50, change_percent: 100 },
  ]),
);
function baseFixture(path: string): unknown {
  const route = path.split("?")[0];
  if (route.includes("/sales-dynamics/client-directory")) return {items:["Клиент1","Клиент2"],next_cursor:null};
  if (route === "/reports/product-analytics/catalog-options/brand") return ["Бренд1", "Бренд2"];
  if (route === "/filters") return { departments: ["Магазин1", "Магазин2"], clients: ["Клиент1","Клиент2"] };
  if (route.endsWith("/managers") && !route.includes("product-sales"))
    return ["Менеджер1", "Менеджер2"];
  if (route.endsWith("/buyer-types")) return ["Розница", "Оптовик"];
  if (route === "/reports/product-sales/sets")
    return [{ id: 1, name: "Набор1", products: [product] }];
  if (route === "/reports/rfm")
    return {
      kpi: { total_clients: 1 },
      segments: [
        {
          segment: "Новые",
          clients: 1,
          client_share: 1,
          revenue: 100,
          revenue_share: 1,
          average_check: 50,
          average_purchases: 2,
          average_recency: 1,
        },
      ],
      clients: {
        items: [client],
        total: 2,
        page: Number(new URLSearchParams(path.split("?")[1]).get("page") || 1),
        pages: 2,
      },
      settings: {
        new_days: 30,
        lost_days: 90,
        sleeping_cycle: 2,
        lost_cycle: 3,
        boundaries: {},
      },
      clients_vr: { metadata_available: true },
    };
  if (route.includes("/rfm/clients/"))
    return [{ month: "2026-01", revenue: 100, purchases: 2 }];
  if (route === "/reports/category-analytics")
    return {
      period,
      previous_period: period,
      year_comparison: {categories: [category], period, metrics, growth_drivers: [], decline_drivers: []},
      metrics,
      categories: [category],
      category_options: ["Категория1"],
      structure: [],
      growth_drivers: [],
      decline_drivers: [],
    };
  if (route.includes("/category-analytics/") && route.endsWith("/details"))
    return {
      category,
      products: [{ ...product, share: 100, revenue_change: 50 }],
      group_by: "day",
      points: [],
    };
  if (route === "/reports/store-analytics")
    return {
      period,
      previous_period: period,
      metrics,
      stores: [{ store: "Магазин1", metrics }],
    };
  if (route.includes("/store-analytics/stores/"))
    return route.endsWith("/dynamics")
      ? { group_by: "day", points: [] }
      : { items: [] };
  if (route === "/reports/sales-dynamics")
    return {
      period,
      requested_period: period,
      previous_period: period,
      metrics,
      chart: { group_by: "day", points: [] },
    };
  if (route === "/reports/promotion-candidates")
    return {
      items: [
        {
          ...product,
          category: "Категория1",
          reasons: ["Продажи падают"],
          stop_factors: [],
          status: "Кандидат",
          units_30: 2,
          units_previous_30: 4,
          units_60_90: 5,
          monthly_units: 2,
        },
      ],
      total: 1,
      table_limit: 500,
      summary: { candidates: 1, stale: 0, excess: 0, decline: 1 },
      catalog_available: true,
      limitations: [],
    };
  if (route === "/reports/promotion-candidates/history")
    return { items: [], message: "История готова" };
  if (route === "/reports/product-sales/summary")
    return { current: { ...product, items_per_check: 5 } };
  if (route === "/reports/product-sales/products") return { items: [product] };
  if (route === "/reports/product-sales/details") return { items: [] };
  return [];
}

function analyticsFixture() {
const product = {
  key: "code:001",
  article: "A-001",
  code: "001",
  name: "Тестовый товар",
  brand: "Бренд A",
  manufacturer: "Завод A",
  category: "Категория",
  subcategory: "Раздел A",
  material: "Стекло",
  revenue: 100,
  previous_revenue: 50,
  revenue_difference: 50,
  revenue_change: 100,
  units: 2,
  previous_units: 1,
  units_difference: 1,
  checks: 1,
  previous_checks: 1,
  checks_change: 0,
  average_price: 50,
  first_sale: "2025-01-01",
  last_sale: "2026-01-01",
  stocks: [{ warehouse: "Склад A", quantity: 7 }],
  prices: [{ price_type: "Розница", value: 55 }],
  properties: [{ name: "Вид товара", value: "10" }],
};
const report = {
  summary: {
    revenue: { current: 100, previous: 50, change_percent: 100 },
    units: { current: 2, previous: 1, change_percent: 100 },
    checks: { current: 1, previous: 1 },
    sku: { current: 1, previous: 1 },
    average_revenue_per_sku: 100,
    average_units_per_sku: 2,
    new: 0,
    growth: 1,
    decline: 0,
    stopped: 0,
  },
  items: [product],
  total: 1,
};

return report;
}
function seasonFixture(detail:boolean) {
const meta = { ready: true, last_success: "2026-10-13", phase: "idle" };
const season = {
  status: "Сезон сейчас",
  confidence: 80,
  reason: "Модель по всей истории",
  period: "10–12",
};
const product = {
  identity: "a".repeat(64),
  product_key: "code:001",
  years: [2015, 2024, 2025],
  seasonality: season,
};
const profile = {
  years: product.years,
  indices: [1],
  historical_indices: [1],
  windows: [],
  historical_windows: [],
  confidence: 80,
  stability: 90,
  trend_percent: 10,
  annual: product.years.map((year) => ({
    year,
    eligible: true,
    total_units: 100,
    peak_month: 11,
    observed_months: [1, 2],
    windows: [],
    weight: year === 2025 ? 1 : 0.5,
  })),
};

return detail?{identity:product.identity,product_key:product.product_key,metadata:meta,profile,seasonality:season,display_scope:"Все магазины",display_note:"Только отображение",months:Array.from({length:24},(_,i)=>({year:2024+Math.floor(i/12),month:i%12+1,units:String(100+i),incomplete_source:false})),next_cursor:null}:{items:[product],next_cursor:null,metadata:meta};
}
export function pdfFixture(path:string):unknown {
 const route=path.split('?')[0],full=path.includes('full=true');
 if(route==='/reports/product-analytics/details') return {product:analyticsFixture().items[0],points:[{period:'2026-10-01',revenue:100,units:2},{period:'2026-10-02',revenue:200,units:4}]};
 if(route==='/reports/product-analytics/report') {const r=analyticsFixture();return {...r,items:Array.from({length:full?550:20},(_,i)=>({...r.items[0],key:`code:${i}`,name:`Тестовый товар ${i}`})),total:550};}
 if(route.startsWith('/reports/product-analytics/catalog-options')) return ['Бренд1'];
 if(route==='/reports/product-analytics/catalog-status') return {enabled:false};
 if(route==='/reports/product-analytics/manager-options') return ['Менеджер1'];
 if(route==='/reports/seasonality'||route.startsWith('/reports/seasonality/')) return seasonFixture(route!=='/reports/seasonality');
 if(route==='/reports/product-sales/catalog/products/search') return {items:[product],total:1,page:1,pages:1};
 if(route==='/reports/product-sales/catalog/filters') return [];
 const r=baseFixture(path) as any;
 if(route==='/reports/promotion-candidates') return {...r,items:Array.from({length:full?500:300},(_,i)=>({...r.items[0],key:`code:${i}`,name:`Тестовый кандидат ${i}`})),total:500,table_limit:full?500:300,summary:{candidates:500,stale:0,excess:0,decline:500}};
 if(route==='/reports/rfm') return {...r,kpi:{total_clients:500,total_revenue:50000,active:500},clients:{...r.clients,items:Array.from({length:full?500:50},(_,i)=>({...client,client_key:`client${i}`,client:`Тестовый клиент ${i}`})),total:500,pages:10}};
 if(route==='/reports/product-sales/products') return {items:Array.from({length:full?550:500},(_,i)=>({...product,key:`code:${i}`,name:`Тестовый товар ${i}`})),total:550};
 if(route==='/reports/product-sales/dynamics') return [{period:'2026-10-01',revenue:100,units:5},{period:'2026-10-02',revenue:200,units:10}];
 if(route==='/reports/sales-dynamics') return {...r,chart:{group_by:'day',points:[{index:0,period:'2026-10-01',current:{revenue:100},previous:{revenue:50}},{index:1,period:'2026-10-02',current:{revenue:200},previous:{revenue:75}}]}};
 return r;
}
