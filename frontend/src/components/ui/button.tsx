import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from 'radix-ui'
import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-control font-medium whitespace-nowrap transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'bg-neel text-on-neel hover:opacity-90',
        secondary: 'border border-line-strong bg-surface text-ink hover:bg-neel-soft',
        ghost: 'text-ink hover:bg-neel-soft',
        // Hard-to-undo actions in a confirmation dialog (remove someone, cancel an invitation).
        danger: 'bg-loss text-surface hover:opacity-90',
        link: 'text-neel underline-offset-4 hover:underline',
      },
      size: {
        md: 'min-h-11 px-4 text-small',
        sm: 'min-h-9 px-3 text-small',
        icon: 'size-11',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** Render the child element (e.g. a router link) with button styling. */
  asChild?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Component = asChild ? Slot.Root : 'button'
    return (
      <Component
        ref={ref}
        type={asChild ? undefined : (type ?? 'button')}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    )
  },
)
Button.displayName = 'Button'
