<script setup lang="ts">
import { computed } from 'vue'

import type { Poem } from '@/types/api'

const props = defineProps<{
  poem: Poem
}>()

const excerpt = computed(() => {
  if (props.poem.summary) {
    return props.poem.summary
  }
  return props.poem.content.replace(/\s+/g, ' ').slice(0, 88)
})
</script>

<template>
  <article class="poem-item">
    <div class="poem-item__meta">
      <span v-if="poem.dynasty_name">{{ poem.dynasty_name }}</span>
      <span>{{ poem.author_name || '佚名' }}</span>
    </div>
    <RouterLink class="poem-item__title" :to="`/poems/${poem.id}`">
      {{ poem.title }}
    </RouterLink>
    <p class="poem-item__excerpt">{{ excerpt }}</p>
    <div class="tag-row">
      <span v-for="category in poem.categories" :key="category.id" class="quiet-tag">
        {{ category.name }}
      </span>
    </div>
  </article>
</template>