<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { authorsApi } from '@/api/authors'
import { getErrorMessage } from '@/api/http'
import type { Author, PaginationMeta } from '@/types/api'

const authors = ref<Author[]>([])
const meta = ref<PaginationMeta>({ page: 1, page_size: 20, total: 0, total_pages: 0 })
const isLoading = ref(true)
const errorMessage = ref('')
const hasNextPage = computed(() => meta.value.page < meta.value.total_pages)
const hasPreviousPage = computed(() => meta.value.page > 1)

async function loadAuthors(page = meta.value.page): Promise<void> {
  isLoading.value = true
  errorMessage.value = ''
  try {
    const result = await authorsApi.list({ page, page_size: meta.value.page_size })
    authors.value = result.items
    meta.value = result.meta
  } catch (error) {
    authors.value = []
    errorMessage.value = getErrorMessage(error)
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  void loadAuthors(1)
})
</script>

<template>
  <div class="page">
    <header class="page-heading">
      <div>
        <p class="section-kicker">作者索引</p>
        <h1>作者</h1>
      </div>
      <p>从作者进入，查看已发布作品与人物简介。</p>
    </header>

    <div v-if="authors.length" class="author-list">
      <RouterLink
        v-for="author in authors"
        :key="author.id"
        class="author-item"
        :to="`/authors/${author.id}`"
      >
        <div class="author-item__seal" aria-hidden="true">{{ author.name.slice(0, 1) }}</div>
        <div>
          <div class="poem-item__meta">
            <span v-if="author.dynasty_name">{{ author.dynasty_name }}</span>
            <span>{{ author.poem_count }} 首</span>
          </div>
          <h2>{{ author.name }}</h2>
          <p>{{ author.bio || '暂无简介' }}</p>
        </div>
      </RouterLink>
    </div>
    <div v-else-if="isLoading" class="empty-state">
      <strong>正在读取作者</strong>
    </div>
    <div v-else-if="errorMessage" class="empty-state">
      <strong>暂时无法读取作者</strong>
      <p>{{ errorMessage }}</p>
      <button class="text-button" type="button" @click="loadAuthors(1)">重试</button>
    </div>
    <div v-else class="empty-state">
      <strong>暂无作者</strong>
      <p>作者与作品发布后会显示在这里。</p>
    </div>

    <nav v-if="meta.total_pages > 1" class="pagination" aria-label="作者分页">
      <button
        type="button"
        class="pagination__button"
        :disabled="!hasPreviousPage || isLoading"
        @click="loadAuthors(meta.page - 1)"
      >
        上一页
      </button>
      <span>第 {{ meta.page }} / {{ meta.total_pages }} 页</span>
      <button
        type="button"
        class="pagination__button"
        :disabled="!hasNextPage || isLoading"
        @click="loadAuthors(meta.page + 1)"
      >
        下一页
      </button>
    </nav>
  </div>
</template>