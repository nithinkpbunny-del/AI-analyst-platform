# Playbook: anomaly review
1. detect_anomalies on the metric for the period, by the dimension most likely to isolate a problem (channel,
   region, store).
2. For each flagged day: how far from its usual level (same weekday over the previous 8 weeks), whether other
   values of the dimension moved too, and whether the days around it are normal.
3. A drop limited to one value for one day suggests an outage or a data gap; a gap of several days with zero rows
   is probably missing data: say so and do not treat it as a real drop.
4. Rank findings by rupee impact; mention at most three.
