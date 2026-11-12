# Playbook: why did a metric change?
1. Pin down the two periods with explicit dates, and check both are complete (fetch_dataset or list_sources).
2. kpi_summary for the metric (and its parts, e.g. orders and average order value) in both periods.
3. driver_analysis over 2-3 dimensions that could explain it (region, channel, category). Find where the change
   is concentrated; note values moving against the total.
4. Go one level deeper inside the biggest mover: driver_analysis again with a filter on it, by another dimension,
   and read new vs repeat customers and orders per customer.
5. Look for a cause in the data (a price change, a discount, a missing week): run_sql if needed.
6. State what happened, then why, with the share of the change each driver explains. Separate one-off effects.
