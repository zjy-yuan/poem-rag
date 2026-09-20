import type { Poem } from '@/data/samplePoems'

export function searchPoems(poems: Poem[], query: string): Poem[] {
  const normalized = query.trim().toLocaleLowerCase('zh-CN')
  if (!normalized) {
    return poems
  }

  return poems.filter((poem) => {
    const searchable = [
      poem.title,
      poem.author,
      poem.dynasty,
      poem.content,
      poem.summary,
      ...poem.categories.map((category) => category.name),
    ]
      .join(' ')
      .toLocaleLowerCase('zh-CN')

    return searchable.includes(normalized)
  })
}

