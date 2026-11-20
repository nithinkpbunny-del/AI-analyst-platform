"""Billing hooks (BUILD_SPEC R7, M18; DECISIONS D117-D121). **No payments, no gateway, no real money.**

What exists: the plan catalog (``plans``), a provider interface with one clearly marked stand-in (``provider``),
usage status against the Managed plan's monthly limit and the once-a-month warnings at 80% and 100% (``limits``),
invoice-ready monthly usage summaries (``summary``) and the preview-then-confirm plan change (``service``).
Plans, limits and usage follow M5 exactly (``app.llm.usage``): the same month, the same count of question and
long-task runs on the platform's key, BYOK without a platform limit.
"""
