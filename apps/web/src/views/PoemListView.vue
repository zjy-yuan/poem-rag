<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import PoemListItem from '@/components/PoemListItem.vue'
import { catalogApi } from '@/api/categories'
import { getErrorMessage } from '@/api/http'
import { poemsApi } from '@/api/poems'
import type { PaginationMeta, Poem, PoemCategory } from '@/types/api'

const query = ref('')
const activeCategoryId = ref<number | null>(null)
const categories = ref<PoemCategory[]>([])
const poems = ref<Poem[]>([])
const currentPage = ref(1)
const meta = ref<PaginationMeta>({ page: 1, page_size: 10, total: 0, total_pages: 0 })
const isLoading = ref(true)
const errorMessage = ref('')
let requestGeneration = 0

const hasNextPage = computed(() => meta.value.page < meta.value.total_pages)
const hasPreviousPage = computed(() => meta.value.page > 1)

async function loadCategories(): Promise<void> {
  try {
    categories.value = await catalogApi.listCategories()
  } catch {
    categories.value = []
  }
}

async function loadPoems(): Promise<void> {
  const generation = ++requestGeneration
  isLoading.value = true
  errorMessage.value = ''
  try {
    const result = await poemsApi.list({
      page: currentPage.value,
      page_size: meta.value.page_size,
      q: query.value.trim() || undefined,
      category_id: activeCategoryId.value ?? undefined,
    })
    if (generation !== requestGeneration) {
      return
    }
    poems.value = result.items
    meta.value = result.meta
  } catch (error) {
    if (generation !== requestGeneration) {
      return
    }
    poems.value = []
    errorMessage.value = getErrorMessage(error)
  } finally {
    if (generation === requestGeneration) {
      isLoading.value = false
    }
  }
}

async function submitSearch(): Promise<void> {
  currentPage.value = 1
  await loadPoems()
}

async function selectCategory(categoryId: number | null): Promise<void> {
  activeCategoryId.value = categoryId
  currentPage.value = 1
  await loadPoems()
}

async function goToPage(page: number): Promise<void> {
  if (page < 1 || page > meta.value.total_pages || page === currentPage.value) {
    return
  }
  currentPage.value = page
  await loadPoems()
}

onMounted(() => {
  void loadCategories()
  void loadPoems()
})
</script>

<template>
  <div class="page">
    <header class="page-heading">
      <div>
        <p class="section-kicker">诗词目录</p>
        <h1>诗词</h1>
      </div>
      <p>按标题、作者、正文和分类筛选已发布作品。</p>
    </header>

    <div class="catalog-toolbar">
      <form class="compact-search" role="search" @submit.prevent="submitSearch">
        <label class="sr-only" for="catalog-search">筛选诗词</label>
        <input
          id="catalog-search"
          v-model="query"
          type="search"
          placeholder="输入标题、作者或正文"
        />
      </form>
      <div class="filter-row" aria-label="体裁和主题筛选">
        <button
          type="button"
          class="filter-button"
          :class="{ 'filter-button--active': activeCategoryId === null }"
          @click="selectCategory(null)"
        >
          全部
        </button>
        <button
          v-for="category in categories"
          :key="category.id"
          type="button"
          class="filter-button"
          :class="{ 'filter-button--active': activeCategoryId === category.id }"
          @click="selectCategory(category.id)"
        >
          {{ category.name }}
        </button>
      </div>
    </div>

    <div v-if="poems.length" class="poem-grid">
      <PoemListItem v-for="poem in poems" :key="poem.id" :poem="poem" />
    </div>
    <div v-else-if="isLoading" class="empty-state">
      <strong>正在读取诗词</strong>
    </div>
    <div v-else-if="errorMessage" class="empty-state">
      <strong>暂时无法读取诗词</strong>
      <p>{{ errorMessage }}</p>
      <button class="text-button" type="button" @click="loadPoems">重试</button>
    </div>
    <div v-else class="empty-state">
      <strong>没有找到匹配的诗词</strong>
      <p>换一个作者、标题或分类再试。</p>
    </div>

    <nav v-if="meta.total_pages > 1" class="pagination" aria-label="诗词分页">
      <button
        type="button"
        class="pagination__button"
        :disabled="!hasPreviousPage || isLoading"
        @click="goToPage(currentPage - 1)"
      >
        上一页
      </button>
      <span>第 {{ meta.page }} / {{ meta.total_pages }} 页</span>
      <button
        type="button"
        class="pagination__button"
        :disabled="!hasNextPage || isLoading"
        @click="goToPage(currentPage + 1)"
      >
        下一页
      </button>
    </nav>
  </div>
</template>