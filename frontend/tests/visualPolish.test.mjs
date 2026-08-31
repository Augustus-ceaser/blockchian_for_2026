import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

test('shared visual system uses one semantic token set and one shell baseline', async () => {
  const [styles, main] = await Promise.all([
    source('../src/styles.css'),
    source('../src/main.tsx'),
  ])

  for (const token of [
    '--color-primary: #087ea4',
    '--color-text: #102a3a',
    '--color-border: #dce6ec',
    '--color-bg-layout: #f5f7fa',
    '--radius-control: 10px',
    '--radius-card: 16px',
    '--shell-bar-height: 68px',
  ]) assert.ok(styles.includes(token), token)

  assert.match(styles, /\.app-sider__brand \{[^}]*height: var\(--shell-bar-height\)/)
  assert.match(styles, /\.app-header \{[\s\S]*?height: var\(--shell-bar-height\)/)
  assert.match(main, /fontFamily: "'Segoe UI Variable'/)
})

test('roadshow focus stays technical but removes decorative motion and card jumping', async () => {
  const styles = await source('../src/styles.css')

  assert.doesNotMatch(styles, /animation:\s*medtrust-orbit/)
  assert.match(styles, /\.roadshow-seal-hero \.ant-typography:not\(h1\)/)
  assert.doesNotMatch(styles, /\.roadshow-seal-hero p\s*\{/)
  assert.doesNotMatch(styles, /\.roadshow-seal-resource:hover\s*\{[^}]*translateY/)
  assert.doesNotMatch(styles, /\.marketplace-card:hover,[\s\S]*?\.marketplace-card:focus-within\s*\{[^}]*translateY/)
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?animation-duration: \.01ms !important/)
})

test('demo workbench, chain and settlement adapt without changing business logic', async () => {
  const styles = await source('../src/styles.css')

  assert.match(styles, /\.role-workbench-card \{[^}]*height: 100%/)
  assert.match(styles, /\.phase58-chain--overview \{[^}]*grid-template-columns: repeat\(6, minmax\(0, 1fr\)\)/)
  assert.match(styles, /\.commerce-settlement-stats \{[^}]*repeat\(auto-fit, minmax\(160px, 1fr\)\)/)
  assert.match(styles, /\.commerce-settlement-card \{[^}]*top: calc\(var\(--shell-bar-height\) \+ 18px\)/)
})
