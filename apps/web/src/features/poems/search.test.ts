import { describe, expect, it } from 'vitest'

import { samplePoems } from '@/data/samplePoems'
import { searchPoems } from './search'

describe('searchPoems', () => {
  it('finds poems by content', () => {
    const result = searchPoems(samplePoems, '明月')
    expect(result.map((poem) => poem.title)).toContain('静夜思')
    expect(result.map((poem) => poem.title)).toContain('水调歌头·明月几时有')
  })

  it('finds poems by author and category', () => {
    expect(searchPoems(samplePoems, '李清照')).toHaveLength(1)
    expect(searchPoems(samplePoems, '思乡')[0]?.title).toBe('天净沙·秋思')
  })

  it('returns all poems for an empty query', () => {
    expect(searchPoems(samplePoems, '  ')).toEqual(samplePoems)
  })
})

