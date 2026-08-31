import assert from 'node:assert/strict'
import test from 'node:test'

import {
  countFromPayload,
  formatCountAnswer,
  isCountQuestion,
  isPublicCatalogQuestion,
  isPublishedProductQuestion,
} from '../src/roadshow/assistantQuery.ts'

test('count questions are distinguished from ordinary resource searches and research demands', () => {
  for (const query of [
    '现在有多少例公共数据集在网站上面呢',
    '平台一共有几个模型？',
    '已发布数据产品总数是多少',
  ]) assert.equal(isCountQuestion(query), true, query)

  for (const query of [
    '查找 CAMELYON17 数据集',
    '帮我找骨折风险模型',
    '我想构建一个骨折患者住院风险预测模型',
  ]) assert.equal(isCountQuestion(query), false, query)
})

test('public catalog scope is detected from the original user wording', () => {
  assert.equal(isPublicCatalogQuestion('现在有多少公共数据集'), true)
  assert.equal(isPublicCatalogQuestion('公开模型有多少'), true)
  assert.equal(isPublicCatalogQuestion('已发布数据产品有多少'), false)
  assert.equal(isPublishedProductQuestion('已发布数据产品有多少'), true)
  assert.equal(isPublishedProductQuestion('公共数据集有多少'), false)
})

test('authoritative count prefers total and otherwise uses the complete items array', () => {
  assert.equal(countFromPayload({ total: 982, items: [{}] }), 982)
  assert.equal(countFromPayload({ items: [{}, {}, {}, {}] }), 4)
  assert.equal(countFromPayload({ total: 0, items: [{}, {}] }), 0)
  assert.equal(countFromPayload({ total: '982', items: [] }), 0)
  assert.equal(countFromPayload({ error: 'unavailable' }), null)
})

test('count answers keep catalog scopes separate and never confuse display limits with totals', () => {
  assert.equal(formatCountAnswer([
    { label: '公共候选数据集', count: 982, unit: '条' },
    { label: '已发布数据产品', count: 4, unit: '项' },
  ]), '已实时读取当前账号可见目录：公共候选数据集 982 条；已发布数据产品 4 项。')
  assert.equal(formatCountAnswer([
    {
      label: '已发布数据产品',
      count: 4,
      unit: '项',
      detail: '外部公共目录元数据产品 3 项、当前可申请产品 1 项',
    },
  ]), '已实时读取当前账号可见目录：已发布数据产品 4 项（外部公共目录元数据产品 3 项、当前可申请产品 1 项）。')
})
