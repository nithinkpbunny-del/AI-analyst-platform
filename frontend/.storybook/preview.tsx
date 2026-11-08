import type { Decorator, Preview } from '@storybook/react-vite'
import { useEffect, type ReactNode } from 'react'
import '../src/lib/i18n'
import '../src/styles/app.css'
import '../src/styles/fonts'

function ThemeFrame({ theme, children }: { theme: 'light' | 'dark'; children: ReactNode }) {
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  return <div className="min-h-dvh bg-paper p-4 text-ink">{children}</div>
}

const withTheme: Decorator = (Story, context) => (
  <ThemeFrame theme={context.globals.theme as 'light' | 'dark'}>
    <Story />
  </ThemeFrame>
)

const preview: Preview = {
  decorators: [withTheme],
  globalTypes: {
    theme: {
      description: 'Colour theme',
      toolbar: {
        title: 'Theme',
        icon: 'mirror',
        items: [
          { value: 'light', title: 'Light' },
          { value: 'dark', title: 'Dark' },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: { theme: 'light' },
  parameters: {
    layout: 'fullscreen',
    controls: { expanded: true },
    a11y: { test: 'error' },
    viewport: {
      options: {
        phone: {
          name: 'Phone (375 px)',
          styles: { width: '375px', height: '812px' },
          type: 'mobile',
        },
        tablet: {
          name: 'Tablet (768 px)',
          styles: { width: '768px', height: '1024px' },
          type: 'tablet',
        },
        desktop: {
          name: 'Desktop (1280 px)',
          styles: { width: '1280px', height: '800px' },
          type: 'desktop',
        },
      },
    },
  },
}

export default preview
