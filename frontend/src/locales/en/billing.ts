// English strings for plans and billing (M18). They load with the screens that show them (Settings > Plan and
// billing, Today's usage notice), not with the app shell (DECISIONS D99). Billing is not switched on: nothing here
// takes a payment, and no price is shown because none exists yet.
const billing = {
  billing: {
    title: 'Plan and billing',
    intro:
      'Your plan, how much of it is used this month, and a summary you can keep for invoicing.',
    notSwitchedOn:
      'Billing is not switched on yet. No payment is taken, no price is set and nothing here can charge you.',
    loading: 'Loading plan and billing…',
    loadFailed: 'Plan and billing could not be loaded. Reload the page to try again.',
    plans: {
      title: 'Plans',
      current: 'Current plan',
      priceSoon: 'Price: coming soon',
      managedLimit: '{{amount}} questions and long analyses a month',
      noLimit: 'No platform question limit',
      change: 'Switch to this plan',
      managed: 'Managed AI',
      byok: 'Your own AI key',
    },
    usage: {
      title: 'This month',
      used: '{{used}} of {{limit}} questions used',
      noLimit: 'No monthly limit: this workspace uses its own AI key.',
      ok: '{{left}} left until {{date}}.',
      warning:
        '{{percent}}% of this month’s questions are used. The count starts again on {{date}}. You can add your own AI key under AI plan to remove the limit.',
      reached:
        'All questions for this month are used. New questions are paused until {{date}}. Add your own AI key under AI plan to keep going.',
      meter: 'Questions used this month',
    },
    change: {
      title: 'Switch to {{plan}}',
      intro: 'Read what changes, then confirm. Nothing is charged.',
      loading: 'Checking what would change…',
      previewFailed: 'Could not check this change. Try again.',
      confirm: 'Switch to {{plan}}',
      confirming: 'Switching…',
      done: 'Your plan is now {{plan}}.',
      blocked: 'This change cannot be made yet',
      blockers: {
        key_missing: 'Add your own AI key first (under AI plan on this page), then switch.',
        managed_unavailable:
          'Managed AI is not set up on this server yet. Add your own AI key instead.',
        same_plan: 'The workspace is already on this plan.',
        unknown_plan: 'That plan does not exist.',
      },
      effects: {
        uses_own_key: 'Questions will use your {{provider}} key ending in {{last4}}.',
        no_platform_limit: 'There is no platform limit on questions.',
        provider_bills_you: 'Your AI provider bills you directly for what you use.',
        monthly_limit: 'The plan includes {{limit}} questions and long analyses a month.',
        month_so_far: 'This month already holds {{used}} of {{limit}}; they count from now on.',
        near_limit: 'That is close to the limit: only a few questions are left this month.',
        already_at_limit:
          'The limit is already used up, so new questions stay paused until {{date}}.',
        effective_now: 'The change takes effect at once.',
        history_kept: 'Your questions, reports, dashboard and usage history stay as they are.',
        not_charged: 'Nothing is charged: billing is not switched on.',
      },
    },
    summary: {
      title: 'Monthly usage summary',
      intro:
        'What the workspace used in a month, in the lines an invoice would carry. It is a summary, not an invoice.',
      month: 'Month',
      draft: 'Draft: the month is not over yet, so these numbers can still change.',
      closing: 'This month has ended. Its final summary is saved within a few minutes.',
      frozen: 'Final summary, saved on {{date}}.',
      plan: 'Plan: {{plan}}.',
      amountDue: 'Amount due',
      amountNone: '₹0 (no price is set yet)',
      download: 'Download CSV',
      downloading: 'Preparing…',
      loading: 'Loading the summary…',
      loadFailed: 'The summary could not be loaded. Try another month or reload.',
      columns: { line: 'Line', quantity: 'Quantity' },
      unlimited: 'No limit',
      changes: 'Plan changes in this month',
      changeRow: '{{from}} to {{to}} on {{date}}',
    },
    events: {
      title: 'What billing has recorded',
      none: 'Nothing yet. Plan changes, usage warnings and monthly summaries are listed here.',
      hint: 'These are kept so billing can be added later. Nothing was sent to a payment provider.',
      plan_changed: 'Plan changed',
      usage_warning: '80% of the monthly questions used',
      usage_limit_reached: 'Monthly question limit reached',
      usage_summary: 'Monthly usage summary saved',
    },
    today: {
      warning: '{{percent}}% of this month’s questions are used ({{used}} of {{limit}}).',
      reached: 'All {{limit}} questions for this month are used. They reset on {{date}}.',
      open: 'See plan and usage',
    },
  },
}

export default billing
