import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AnswerSheet } from './AnswerSheet'
import { ConnectionCard } from './ConnectionCard'
import { EmptyState } from './EmptyState'
import { Highlight } from './Highlight'
import { KpiTile } from './KpiTile'
import { ProgressSteps } from './ProgressSteps'
import { QualityBanner } from './QualityBanner'
import { ScenarioCard } from './ScenarioCard'
import { SourceMark } from './SourceMark'
import { Button } from './ui/button'

const now = new Date('2026-11-09T10:00:00Z')

describe('Highlight', () => {
  it('renders the text with a decorative mark that screen readers skip', () => {
    render(<Highlight>−18%</Highlight>)
    expect(screen.getByText('−18%')).toBeInTheDocument()
    expect(screen.getByTestId('haldi-mark')).toHaveAttribute('aria-hidden')
  })

  it('plays the sweep only when asked', () => {
    const { rerender } = render(<Highlight>₹12.4 L</Highlight>)
    expect(screen.getByTestId('haldi-mark').style.animation).toContain('haldi-sweep')
    rerender(<Highlight sweep={false}>₹12.4 L</Highlight>)
    expect(screen.getByTestId('haldi-mark').style.animation).toBe('')
  })

  it('warns in development when more than one mark is visible', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    render(
      <>
        <Highlight>one</Highlight>
        <Highlight>two</Highlight>
      </>,
    )
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('one per view'))
  })
})

describe('SourceMark', () => {
  it('opens the provenance popover with source, query and freshness', async () => {
    render(
      <SourceMark
        index={1}
        now={now}
        source={{
          connection: 'Shopify',
          dataset: 'orders',
          query: 'Revenue by region, Jul–Sep 2026',
          fetchedAt: '2026-11-09T09:58:00Z',
          rowCount: 28540,
        }}
      />,
    )
    const trigger = screen.getByRole('button', {
      name: /Source 1: Shopify orders, fetched 2 min ago/,
    })
    await userEvent.click(trigger)
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Shopify · orders')
    expect(dialog).toHaveTextContent('Revenue by region, Jul–Sep 2026')
    expect(dialog).toHaveTextContent('Fetched 2 min ago · 28,540 rows')
  })
})

describe('QualityBanner', () => {
  it('is announced as a data-quality note', () => {
    render(<QualityBanner>October data is 60% complete; numbers may be low.</QualityBanner>)
    expect(screen.getByRole('note')).toHaveTextContent('Data quality: October data is 60% complete')
  })
})

describe('ProgressSteps', () => {
  it('lists each step with its status for screen readers', () => {
    render(
      <ProgressSteps
        steps={[
          { id: 'a', label: 'Checking connected sources', status: 'done' },
          { id: 'b', label: 'Fetching Shopify orders', status: 'active' },
          { id: 'c', label: 'Comparing Q3 with Q2', status: 'pending' },
        ]}
      />,
    )
    const items = within(screen.getByRole('list', { name: 'Progress' })).getAllByRole('listitem')
    expect(items.map((i) => i.textContent)).toEqual([
      'Checking connected sources (done)',
      'Fetching Shopify orders (in progress)',
      'Comparing Q3 with Q2 (waiting)',
    ])
  })
})

describe('KpiTile', () => {
  it('formats rupees and says the change in words', () => {
    render(
      <KpiTile
        label="Revenue"
        value={1240000}
        format="inr"
        deltaPct={12.4}
        comparison="last week"
      />,
    )
    const tile = screen.getByRole('article', { name: 'Revenue' })
    expect(tile).toHaveTextContent('₹12.4 L')
    expect(tile).toHaveTextContent('Up 12.4% vs last week')
    expect(
      within(tile)
        .getByText(/12.4% vs last week/)
        .closest('p'),
    ).toHaveClass('text-gain')
  })

  it('treats a fall as good news when lower is better', () => {
    render(<KpiTile label="Refunds" value={52} deltaPct={-8} goodDirection="down" />)
    expect(screen.getByText(/8.0% vs last period/).closest('p')).toHaveClass('text-gain')
  })

  it('marks a fall in revenue as a loss', () => {
    render(
      <KpiTile label="South revenue" value={2801265} format="inr" deltaPct={-17.7} highlight />,
    )
    expect(screen.getByText(/17.7%/).closest('p')).toHaveClass('text-loss')
    expect(screen.getByTestId('haldi-mark')).toBeInTheDocument()
  })
})

describe('ScenarioCard', () => {
  const props = {
    title: '10% off Fashion in October',
    horizon: 'next 3 months',
    lever: {
      label: 'Discount',
      value: 10,
      min: 0,
      max: 30,
      step: 1,
      format: (v: number) => `${v}%`,
    },
    baseline: 1_86_00_000,
    scenario: { expected: 1_94_00_000, low: 1_88_00_000, high: 2_01_00_000 },
    assumptions: ['Price elasticity −1.4, from your Oct 2025 sale', 'No change in marketing spend'],
    confidence: { level: 'medium' as const, reason: 'Based on one past discount period.' },
  }

  it('shows baseline, expected value, range, assumptions and confidence', () => {
    render(<ScenarioCard {...props} onLeverChange={() => {}} />)
    const card = screen.getByRole('region', { name: props.title })
    expect(card).toHaveTextContent('₹1.86 Cr')
    expect(card).toHaveTextContent('₹1.94 Cr')
    expect(card).toHaveTextContent('Likely between ₹1.88 Cr and ₹2.01 Cr')
    expect(card).toHaveTextContent('Medium confidence')
    expect(card).toHaveTextContent('No change in marketing spend')
  })

  it('reports lever moves from the keyboard (no LLM call needed)', async () => {
    const onLeverChange = vi.fn()
    render(<ScenarioCard {...props} onLeverChange={onLeverChange} />)
    const thumb = screen.getByRole('slider', { name: 'Discount' })
    thumb.focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(onLeverChange).toHaveBeenCalledWith(11)
  })
})

describe('ConnectionCard', () => {
  it.each([
    ['active', 'Live'],
    ['needs_reauth', 'Needs sign-in'],
    ['error', 'Error'],
    ['disabled', 'Paused'],
  ] as const)('shows %s as "%s"', (status, label) => {
    render(<ConnectionCard name="UrbanCart store" sourceType="Shopify" status={status} />)
    expect(screen.getByRole('article', { name: 'UrbanCart store' })).toHaveTextContent(label)
  })

  it('shows freshness and the fix-it message', () => {
    render(
      <ConnectionCard
        name="Targets"
        sourceType="Google Sheets"
        status="needs_reauth"
        lastSuccessAt="2026-11-08T08:00:00Z"
        now={now}
        message="Google stopped sharing this sheet. Sign in to Google again to reconnect."
        actions={<Button>Reconnect Google Sheets</Button>}
      />,
    )
    expect(screen.getByText('Updated yesterday')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reconnect Google Sheets' })).toBeInTheDocument()
  })
})

describe('EmptyState', () => {
  it('shows one sentence and one action', () => {
    render(
      <EmptyState
        message="Connect your first data source to start asking questions."
        action={<Button>Connect data</Button>}
      />,
    )
    expect(
      screen.getByText('Connect your first data source to start asking questions.'),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })
})

describe('AnswerSheet', () => {
  it('renders the answer format and wires the actions', async () => {
    const onPin = vi.fn()
    const onNextQuestion = vi.fn()
    render(
      <AnswerSheet
        lead={
          <>
            South revenue fell <Highlight>18%</Highlight> in Q3.
          </>
        }
        evidence={[{ id: '1', content: 'Q2 ₹34.0 L → Q3 ₹28.0 L' }]}
        dataNotes={['Website orders for 9–15 March are missing.']}
        nextQuestions={['Which products drove the drop?']}
        onNextQuestion={onNextQuestion}
        onPin={onPin}
        onShowWork={() => {}}
      />,
    )
    const answer = screen.getByRole('article', { name: 'Answer' })
    expect(answer).toHaveTextContent('South revenue fell 18% in Q3.')
    expect(within(answer).getByRole('region', { name: 'Data notes' })).toHaveTextContent('missing')
    await userEvent.click(screen.getByRole('button', { name: 'Pin' }))
    expect(onPin).toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Which products drove the drop?' }))
    expect(onNextQuestion).toHaveBeenCalledWith('Which products drove the drop?')
    expect(screen.queryByRole('button', { name: 'Export' })).not.toBeInTheDocument()
  })
})
