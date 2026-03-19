# UI Redesign Plan (User Input)

Date: 2026-03-13

This file stores the provided redesign instruction as a persistent workspace reference.

## Goal

Transform the current dark admin UI into a modern light design while keeping all existing functionality.

## Execution Summary

1. Update design tokens (`:root`) to light palette.
2. Add modern typography (Google Fonts: `Sora`, `Space Mono`).
3. Redesign layout visuals (`body`, container, header, cards, tables, buttons, badges).
4. Add smooth animations and responsive behavior.
5. Verify desktop/tablet/mobile rendering and console cleanliness.

## Critical Color Direction

- Old: dark background/cards, light text.
- New:
  - `--bg-primary: #FAFBFD`
  - `--bg-secondary: #F3F4F7`
  - `--bg-tertiary: #FFFFFF`
  - `--text-primary: #1A1F3A`
  - `--border: #E5E7EB`
  - Accent: violet/cyan/pink/emerald/amber.

## Mandatory Tokens (as provided)

```css
:root {
    --primary: #5B5FFF;
    --primary-light: #7B7FFF;
    --primary-dark: #3B3FFF;
    --accent-cyan: #00D4FF;
    --accent-pink: #FF6B9D;
    --accent-emerald: #10B981;
    --accent-amber: #F59E0B;
    
    --bg-primary: #FAFBFD;
    --bg-secondary: #F3F4F7;
    --bg-tertiary: #FFFFFF;
    --bg-hover: #F1F3F7;
    
    --text-primary: #1A1F3A;
    --text-secondary: #6B7280;
    --text-tertiary: #9CA3AF;
    
    --border: #E5E7EB;
    --border-light: #F3F4F7;
    
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
    --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.07);
    --shadow-lg: 0 10px 25px rgba(0, 0, 0, 0.08);
}
```

## Required Head Addition

```html
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
```

## Safety Checklist for Implementation

- Keep HTML structure and JS IDs unchanged (to avoid breaking API-driven widgets).
- Restrict changes mostly to style layer (`admin.css`, base head/fonts).
- Validate after changes:
  - no runtime template errors,
  - `pytest -q` passes,
  - smoke checks pass,
  - health endpoints remain OK.

