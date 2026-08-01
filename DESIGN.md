# (8) Ads Manager

## Mission
Create implementation-ready, token-driven UI guidance for (8) Ads Manager that is optimized for consistency, accessibility, and fast delivery across dashboard web app.

## Brand
- Product/brand: (8) Ads Manager
- URL: https://adsmanager.facebook.com/adsmanager/manage/adsets?act=235317476491502&columns=name%2Cdelivery%2Crecommendations_guidance%2Cresults%2Ccost_per_result%2Cbudget%2Cspend%2Cimpressions%2Creach%2Cschedule%2Cend_time%2Cattribution_setting%2Cbid%2Clast_significant_edit%2Cquality_score_organic%2Cquality_score_ectr%2Cquality_score_ecvr%2Ccampaign_name%2Cactions%3Aonsite_conversion.messaging_first_reply%2Ccost_per_action_type%3Aonsite_conversion.messaging_first_reply%2Cactions%3Aonsite_conversion.messaging_conversation_started_7d%2Ccost_per_action_type%3Aonsite_conversion.messaging_conversation_started_7d&attribution_windows=default&date=2023-06-22_2026-07-13%2Cmaximum&insights_date=2023-06-15_2026-07-13%2Cmaximum&selected_campaign_ids=52514541423243&selected_adset_ids=52515761933843&selected_ad_ids=52515761934243&treenav=true&nav_source=no_referrer
- Audience: authenticated users and operators
- Product surface: dashboard web app

## Style Foundations
- Visual style: structured, tokenized, content-first
- Main font style: `font.family.primary=Roboto`, `font.family.stack=Roboto, Arial, sans-serif`, `font.size.base=12px`, `font.weight.base=400`, `font.lineHeight.base=15.36px`
- Typography scale: `font.size.xs=12px`, `font.size.sm=14px`
- Color palette: `color.text.primary=#1c2b33`, `color.text.secondary=#1c1e21`, `color.text.tertiary=#385898`, `color.text.inverse=#0a78be`, `color.surface.base=#000000`, `color.surface.muted=#ffffff`, `color.surface.strong=#006b4e`, `color.border.muted=#cbd2d9`
- Spacing scale: `space.1=1px`, `space.2=2px`, `space.3=4px`, `space.4=5px`, `space.5=6px`, `space.6=7px`, `space.7=8px`, `space.8=10px`
- Radius/shadow/motion tokens: `radius.xs=3px`, `radius.sm=4px`, `radius.md=999px` | `shadow.1=rgba(0, 0, 0, 0.1) 0px 2px 8px 0px, rgba(0, 0, 0, 0.1) 0px 1px 1px 0px` | `motion.duration.instant=150ms`, `motion.duration.fast=250ms`, `motion.duration.normal=300ms`

## Accessibility
- Target: WCAG 2.2 AA
- Keyboard-first interactions required.
- Focus-visible rules required.
- Contrast constraints required.

## Writing Tone
Concise, confident, implementation-focused.

## Rules: Do
- Use semantic tokens, not raw hex values, in component guidance.
- Every component must define states for default, hover, focus-visible, active, disabled, loading, and error.
- Component behavior should specify responsive and edge-case handling.
- Interactive components must document keyboard, pointer, and touch behavior.
- Accessibility acceptance criteria must be testable in implementation.

## Rules: Don't
- Do not allow low-contrast text or hidden focus indicators.
- Do not introduce one-off spacing or typography exceptions.
- Do not use ambiguous labels or non-descriptive actions.
- Do not ship component guidance without explicit state rules.

## Guideline Authoring Workflow
1. Restate design intent in one sentence.
2. Define foundations and semantic tokens.
3. Define component anatomy, variants, interactions, and state behavior.
4. Add accessibility acceptance criteria with pass/fail checks.
5. Add anti-patterns, migration notes, and edge-case handling.
6. End with a QA checklist.

## Required Output Structure
- Context and goals.
- Design tokens and foundations.
- Component-level rules (anatomy, variants, states, responsive behavior).
- Accessibility requirements and testable acceptance criteria.
- Content and tone standards with examples.
- Anti-patterns and prohibited implementations.
- QA checklist.

## Component Rule Expectations
- Include keyboard, pointer, and touch behavior.
- Include spacing and typography token requirements.
- Include long-content, overflow, and empty-state handling.
- Include known page component density: buttons (71), links (17), inputs (6), navigation (1), lists (1).

- Extraction diagnostics: Limited typography variety detected; size scale may need manual refinement. Audience and product surface inference confidence is low; verify generated brand context.

## Quality Gates
- Every non-negotiable rule must use "must".
- Every recommendation should use "should".
- Every accessibility rule must be testable in implementation.
- Teams should prefer system consistency over local visual exceptions.