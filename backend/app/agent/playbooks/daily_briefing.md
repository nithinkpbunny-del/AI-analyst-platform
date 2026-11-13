# Playbook: daily briefing and health check
1. kpi_summary for the main metrics (revenue, orders, average order value when they exist) over the latest complete
   period the data covers, against the period before; say which dates the data runs to.
2. detect_anomalies on revenue (or orders) over the last 7 days of data, by the dimension most likely to isolate a
   problem (channel, region, store). Mention at most three unusual numbers, largest rupee impact first.
3. growth_signals on the same metric over the last 12 months: report segments that grew several periods in a row
   while gaining share, and how much came from repeat customers. These are "working well" items.
4. Suggested changes only from these findings, each with its rupee impact from a tool result (R9 rules); use
   what_if only with its range and labelled assumptions.
5. When nothing is notable, give a short business-health summary: the main metrics and their change, nothing more.
6. Always state data-quality gaps and how old the data is.
