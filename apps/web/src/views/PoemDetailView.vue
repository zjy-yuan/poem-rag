<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiClientError, getErrorMessage } from '@/api/http'
import { poemsApi } from '@/api/poems'
import type { Poem } from '@/types/api'

const route = useRoute()
const poem = ref<Poem | null>(null)
const isLoading = ref(true)
const notFound = ref(false)
const errorMessage = ref('')
const lines = computed(() => poem.value?.content.split('\n') ?? [])

async function loadPoem(): Promise<void> {
  isLoading.value = true
  notFound.value = false
  errorMessage.value = ''
  try {
    poem.value = await poemsApi.get(Number(route.params.poemId))
  } catch (error) {
    poem.value = null
    if (error instanceof ApiClientError && error.code === 'POEM_NOT_FOUND') {
      notFound.value = true
    } else {
      errorMessage.value = getErrorMessage(error)
    }
  } finally {
    isLoading.value = false
  }
}

watch(() => route.params.poemId, loadPoem, { immediate: true })
</script>

<template>
  <div v-if="isLoading" class="page">
    <div class="empty-state">
      <strong>正在读取诗词</strong>
    </div>
  </div>

  <div v-else-if="poem" class="page poem-detail">
    <nav class="breadcrumb" aria-label="面包屑">
      <RouterLink to="/poems">诗词</RouterLink>
      <span>/</span>
      <span>{{ poem.title }}</span>
    </nav>

    <article>
      <header class="poem-detail__header">
        <div class="poem-item__meta">
          <span v-if="poem.dynasty_name">{{ poem.dynasty_name }}</span>
          <RouterLink v-if="poem.author_id" :to="`/authors/${poem.author_id}`">
            {{ poem.author_name }}
          </RouterLink>
          <span v-else>佚名</span>
        </div>
        <h1>{{ poem.title }}</h1>
        <div class="tag-row">
          <span v-for="category in poem.categories" :key="category.id" class="quiet-tag">
            {{ category.name }}
          </span>
          <span v-for="tag in poem.tags" :key="tag.id" class="quiet-tag">
            {{ tag.name }}
          </span>
        </div>
      </header>

      <div class="poem-text">
        <p v-for="(line, index) in lines" :key="index">{{ line }}</p>
      </div>

      <aside v-if="poem.summary" class="poem-note">
        <p class="section-kicker">简释</p>
        <p>{{ poem.summary }}</p>
      </aside>
    </article>
  </div>

  <div v-else class="page empty-state">
    <strong>{{ notFound ? '没有找到这首诗词' : '暂时无法读取诗词' }}</strong>
    <p v-if="errorMessage">{{ errorMessage }}</p>
    <button v-if="errorMessage" class="text-button" type="button" @click="loadPoem">重试</button>
    <RouterLink v-else class="text-link" to="/poems">返回诗词目录</RouterLink>
  </div>
</template>