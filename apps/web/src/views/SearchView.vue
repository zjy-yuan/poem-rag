<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PoemListItem from '@/components/PoemListItem.vue'
import { getErrorMessage } from '@/api/http'
import { poemsApi } from '@/api/poems'
import type { PaginationMeta, Poem } from '@/types/api'

const route = useRoute()
const router = useRouter()
const query = ref(typeof route.query.q === 'string' ? route.query.q : '')
const results = ref<Poem[]>([])
const meta = ref<PaginationMeta>({ page: 1, page_size: 20, total: 0, total_pages: 0 })
const isLoading = ref(true)
const errorMessage = ref('')
let requestGeneration = 0

const resultLabel = computed(() =>
  query.value.trim() ? `“${query.value.trim()}”的结果` : '全部诗词',
)

async function runSearch(): Promise<void> {
  const generation = ++requestGeneration
  isLoading.value = true
  errorMessage.value = ''
  try {
    const normalizedQuery = query.value.trim()
    const result = normalizedQuery
      ? await poemsApi.search({ q: normalizedQuery, page: 1, page_size: 20 })
      : await poemsApi.list({ page: 1, page_size: 20 })
    if (generation !== requestGeneration) {
      return
    }
    results.value = result.items
    meta.value = result.meta
  } catch (error) {
    if (generation !== requestGeneration) {
      return
    }
    results.value = []
    errorMessage.value = getErrorMessage(error)
  } finally {
    if (generation === requestGeneration) {
      isLoading.value = false
    }
  }
}

async function submit(): Promise<void> {
  const nextQuery = query.value.trim()
  await router.replace({
    name: 'search',
    query: nextQuery ? { q: nextQuery } : {},
  })
  await runSearch()
}

watch(
  () => route.query.q,
  (value) => {
    const nextQuery = typeof value === 'string' ? value : ''
    if (nextQuery === query.value) {
      return
    }
    query.value = nextQuery
    void runSearch()
  },
)

onMounted(() => {
  void runSearch()
})
</script>

<template>
  <div class="page">
    <header class="page-heading page-heading--compact">
      <div>
        <p class="section-kicker">全库检索</p>
        <h1>搜索</h1>
      </div>
      <p>结果会同时匹配标题、正文、作者、朝代、体裁与主题。</p>
    </header>

    <form class="inline-search" role="search" @submit.prevent="submit">
      <label class="sr-only" for="search-page-input">搜索诗词</label>
      <input
        id="search-page-input"
        v-model="query"
        type="search"
        placeholder="输入关键词"
        autocomplete="off"
      />
      <button type="submit">搜索</button>
    </form>

    <p class="result-count">
      {{ resultLabel }}
      <span>{{ meta.total }} 首</span>
    </p>

    <div v-if="results.length" class="poem-grid">
      <PoemListItem v-for="poem in results" :key="poem.id" :poem="poem" />
    </div>
    <div v-else-if="isLoading" class="empty-state">
      <strong>正在搜索</strong>
    </div>
    <div v-else-if="errorMessage" class="empty-state">
      <strong>搜索暂时不可用</strong>
      <p>{{ errorMessage }}</p>
      <button class="text-button" type="button" @click="runSearch">重试</button>
    </div>
    <div v-else class="empty-state">
      <strong>没有找到匹配内容</strong>
      <p>尝试使用诗句中的词语、作者姓名或朝代。</p>
    </div>
  </div>
</template>