# Playbook: goals and targets
1. goal_progress with no arguments: every goal set in the app (list_sources lists them), with actual so far, the
   projection to the end of the period, the gap, what is needed per day and a status decided by code.
2. For a target the person names that is not a saved goal, call goal_progress with metric, target and period.
3. Targets may also live in a table of their own (e.g. a Targets sheet by month and region); find it with
   list_sources and compare with run_sql (actual and target for the same months and dimension).
4. Report actual vs target and the projected gap in rupees and %; say plainly which goals are at risk or behind and
   what is needed per day to recover. Use the status from the tool; never decide it yourself.
5. For a goal behind plan, dig one level deeper (driver_analysis on the metric for the period so far) to say why.
