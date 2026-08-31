# Phase 5.12.7-H2 Readiness Overflow Diagnosis

## Scope

This diagnosis covers the accepted Z2E readiness detail route:

```text
/execution/7635e4f1-04a8-5510-a510-af5982c6b125
```

It does not change a business record, API, database schema, state machine,
authorization rule, execution result, or audit event.

## Root Cause

The overflow did not originate in the H1 confirmation form. All existing Z2E
contracts have completed provider readiness, so that conditional form was not
rendered during the failure.

The mobile `Descriptions` component contained a table with an intrinsic width
of about 290 pixels. Although the stylesheet intended to use fixed table
layout, Ant Design's generated rule kept the computed layout at `auto`. The
descriptions viewport was narrower than the table and clipped it:

| Viewport | Descriptions viewport | Table |
|---:|---:|---:|
| 320px | 158.4px | 290px |
| 360px | 183.2px | 290px |
| 375px | 198.4px | 290px |
| 390px | 213.6px | 290px |
| 412px | 235.2px | 290px |

At 320px, two additional constraints mattered:

- the global `html, body, #root` minimum width remained 320px while the browser
  reserved space for its vertical scrollbar;
- the `hard_isolation=false` tag was an unbreakable inline item.

The earlier 27px page-level symptom depended on the browser's viewport and
sidebar transition. The stable defect was the descriptions table's intrinsic
width and clipping.

## Fix

The H2 change is limited to the existing mobile breakpoint:

- allow the root elements to shrink below the old global minimum;
- constrain the descriptions viewport and direct table to the available width;
- enforce fixed layout on the mobile descriptions table;
- allow labels, values, and the scoped status tag to wrap;
- reduce only the mobile detail card's horizontal padding.

The fix does not use a global `overflow-x: hidden` rule. Content is reflowed
instead of concealed.

## Regression Guard

The readiness test asserts the mobile selectors and containment rules,
including the fixed table layout. It also rejects a global root-level
horizontal-overflow hiding rule.

Implementation commit:

```text
13eb282 fix: prevent readiness details overflow on narrow screens
```

