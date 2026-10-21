"""Shared purchase-cycle definitions; SQL implementation for bounded shadow batches.

RFM v1 retains all-check days/mean and its legacy key. Shadow uses positive-check
days and v1 analytical keys. Both use the same distinct-day interval definition.
"""
from datetime import date
from statistics import mean, median, quantiles
from sqlalchemy import text

QUANTILES = (0.25, 0.50, 0.75)


def interval_statistics(dates):
    days = sorted(set(dates))
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    if not gaps:
        return dict(mean_interval=None, median_interval=None, q25=None, q75=None, mad=None,
                    gap_count=0, distinct_purchase_days=len(days))
    q25, mid, q75 = quantiles(gaps, n=4, method="inclusive") if len(gaps) > 1 else [gaps[0]] * 3
    return dict(mean_interval=float(mean(gaps)), median_interval=mid, q25=q25, q75=q75,
                mad=median(abs(g - mid) for g in gaps), gap_count=len(gaps),
                distinct_purchase_days=len(days))


async def load_inactivity_features(db, run_id, keys, as_of):
    """One set query. No sale/item hydration or purchase-date arrays in Python."""
    result = await db.execute(text("""
    WITH facts AS (
      SELECT * FROM analytics_purchase_days
      WHERE run_id=:run AND client_key=ANY(CAST(:keys AS text[]))
    ), days AS (
      SELECT client_key,sale_date,
        sale_date-lag(sale_date) OVER(PARTITION BY client_key ORDER BY sale_date) AS gap
      FROM facts WHERE positive_checks>0 AND sale_date>=CAST(:asof AS date)-364
    ), stats AS (
      SELECT client_key,count(*) AS distinct_purchase_days,count(gap) AS gap_count,
        max(sale_date)-min(sale_date) AS history_span_days,
        avg(gap) AS mean_interval,
        percentile_cont(0.25) WITHIN GROUP(ORDER BY gap) AS q25,
        percentile_cont(0.50) WITHIN GROUP(ORDER BY gap) AS median_interval,
        percentile_cont(0.75) WITHIN GROUP(ORDER BY gap) AS q75
      FROM days GROUP BY client_key
    ), deviations AS (
      SELECT d.client_key,percentile_cont(0.50) WITHIN GROUP(ORDER BY abs(d.gap-s.median_interval)) AS mad
      FROM days d JOIN stats s USING(client_key) WHERE d.gap IS NOT NULL GROUP BY d.client_key
    ), amounts AS (
      SELECT client_key,min(display_name) AS display_name,
        min(sale_date) FILTER(WHERE positive_checks>0) AS first_purchase_date,
        max(sale_date) FILTER(WHERE positive_checks>0) AS last_purchase_date,
        sum(revenue) FILTER(WHERE sale_date>=CAST(:asof AS date)-364) AS revenue,
        sum(checks) FILTER(WHERE sale_date>=CAST(:asof AS date)-364) AS frequency,
        sum(revenue) FILTER(WHERE sale_date>=CAST(:asof AS date)-179) AS reference_revenue_180
      FROM facts GROUP BY client_key
    ) SELECT a.*,s.distinct_purchase_days,s.gap_count,s.history_span_days,
       s.mean_interval,s.median_interval,s.q25,s.q75,d.mad,
       a.revenue/nullif(a.frequency,0) AS average_check
      FROM amounts a LEFT JOIN stats s USING(client_key) LEFT JOIN deviations d USING(client_key)
      ORDER BY a.client_key
    """), {"run": run_id, "keys": keys, "asof": as_of})
    return [dict(row) for row in result.mappings()]
