// Every shared component is documented in light, dark and phone width (BUILD_SPEC 3: Storybook).
export const dark = { globals: { theme: 'dark' } } as const
export const phone = { globals: { viewport: { value: 'phone', isRotated: false } } } as const
