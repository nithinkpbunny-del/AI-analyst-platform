import { Highlight } from '../components/Highlight'
import { SourceMark, type Provenance } from '../components/SourceMark'

// Figures from the UrbanCart demo company (eval/fixtures/ANSWER_KEY.md), so stories look like real answers.
export const now = new Date('2026-11-09T06:00:00+05:30')
export const ordersSource: Provenance = {
  connection: 'Shopify',
  dataset: 'orders',
  query: 'Revenue by region, Apr–Sep 2026',
  fetchedAt: new Date(now.getTime() - 2 * 60_000),
  rowCount: 28540,
}
export const targetsSource: Provenance = {
  connection: 'Google Sheets',
  dataset: 'targets',
  query: 'Regional revenue targets, Q3 2026',
  fetchedAt: new Date(now.getTime() - 9 * 60_000),
  rowCount: 84,
}

export const southLead = (
  <>
    South revenue fell <Highlight>17.7%</Highlight> in Q3 while every other region grew.
  </>
)

export const southEvidence = [
  {
    id: 'q',
    content: (
      <span>
        South: ₹34.0 L in Q2 → ₹28.0 L in Q3
        <SourceMark index={1} source={ordersSource} now={now} />
      </span>
    ),
  },
  {
    id: 'cause',
    content: (
      <span>
        Electronics prices rose 12% in the South on 1 July; existing customers there now order 41%
        less often
        <SourceMark index={2} source={ordersSource} now={now} />
      </span>
    ),
  },
  {
    id: 'target',
    content: (
      <span>
        Q3 target missed by 25.1%; North, East and West beat theirs
        <SourceMark index={3} source={targetsSource} now={now} />
      </span>
    ),
  },
]
