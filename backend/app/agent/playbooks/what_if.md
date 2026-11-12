# Playbook: what-if
1. Turn the question into one scenario: the lever (`price`, `discount` or `marketing_spend`), the change (percent for
   price and spend, percentage points for a discount), the scope (a region, product type or channel) and the
   horizon in months (default 3). If the lever or the size of the change is missing, ask for it; do not guess.
   Stock, staffing and product-mix scenarios are not supported: say so, and offer the nearest lever.
2. Call `what_if` once. Do not do the arithmetic yourself; the tool returns the baseline, the scenario, the
   low/expected/high range, the assumptions, the side effects and the confidence.
3. Lead with the expected change and its range ("about +₹4.1 L over 3 months, between +₹1.2 L and +₹6.8 L").
   Never give the expected figure without the range.
4. State each assumption in plain words and say where it came from: `history` (learned from this business's own
   past), `default` (an industry assumption, because the history could not tell) or `you` (the person's own).
   If an assumption is a default, say so clearly and invite the person to give their own number.
5. Name the side effects the tool returned (units sold, discount given away, extra spend, share of revenue from
   repeat customers) and the confidence with its reason.
6. If the result looks surprising (for example a price rise that raises revenue because customers barely react),
   say that the estimate comes from the past and may not hold for a larger change.
7. End with one next question (for example, the same change in another region, or a smaller step).
