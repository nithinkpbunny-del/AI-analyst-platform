import type { IndustryTemplate } from '../lib/teamApi'

export const demoTemplates: IndustryTemplate[] = [
  {
    key: 'retail',
    name: 'Retail',
    summary: 'Shops and stores that sell to walk-in customers.',
    words: 'bills (not orders), stores or outlets, shoppers.',
    kpis: [
      {
        key: 'revenue',
        label: 'Sales',
        meaning: 'Sales value after discounts and returns.',
        unit: 'INR',
        good_direction: 'up',
      },
      {
        key: 'orders',
        label: 'Bills',
        meaning: 'Number of bills raised.',
        unit: 'count',
        good_direction: 'up',
      },
    ],
    starter_questions: [
      'Which store or category drove the change in sales last month?',
      'Which items are selling slowly and tying up stock?',
      'What were the busiest and quietest days last month?',
      'How much margin are we giving away in discounts?',
    ],
  },
  {
    key: 'restaurant',
    name: 'Restaurant',
    summary: 'Restaurants, cafes and cloud kitchens.',
    words: 'bills or covers, outlets, tables.',
    kpis: [
      {
        key: 'food_cost_pct',
        label: 'Food cost',
        meaning: 'Cost of ingredients as a share of sales.',
        unit: 'percent',
        good_direction: 'down',
      },
    ],
    starter_questions: [
      'Which outlet or day of the week sold the most last month?',
      'Is food cost rising, and which menu items push it up?',
      'How much of our sales come through delivery apps?',
    ],
  },
  {
    key: 'other',
    name: 'Something else',
    summary: 'A general starting point for any other kind of business.',
    words: 'the words your data uses.',
    kpis: [
      {
        key: 'revenue',
        label: 'Revenue',
        meaning: 'Sales value after discounts and refunds.',
        unit: 'INR',
        good_direction: 'up',
      },
    ],
    starter_questions: ['What can you tell me about my data?'],
  },
]
