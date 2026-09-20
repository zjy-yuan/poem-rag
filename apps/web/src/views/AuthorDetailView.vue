<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { authorsApi } from '@/api/authors'
import { getErrorMessage } from '@/api/http'
import type { AuthorDetail } from '@/types/api'

const route = useRoute()
const author = ref<AuthorDetail | null>(null)
const isLoading = ref(true)
const errorMessage = ref('')

async function loadAuthor(): Promise<void> {
  isLoading.value = true
  errorMessage.value = ''
  try {
    author.value = await authorsApi.get(Number(route.params.authorId))
  } catch (error) {
    author.value = null
    errorMessage.value = getErrorMessage(error)
  } finally {
    isLoading.value = false
  }
}

watch(() => route.params.authorId, loadAuthor, { immediate: true })
</script>

<template>
  <div v-if="isLoading" class="page">
    <div class="empty-state">
      <strong>正在读取作者</strong>
    </div>
  </div>

  <div v-else-if="author" class="page author-detail">
    <nav class="breadcrumb" aria-label="面包屑">
      <RouterLink to="/authors">作者</RouterLink>
      <span>/</span>
      <span>{{ author.name }}</span>
    </nav>

    <header class="page-heading">
      <div>
        <p class="section-kicker">{{ author.dynasty_name || '历代' }}</p>
        <h1>{{ author.name }}</h1>
      </div>
      <p>{{ author.bio || '暂无人物简介。' }}</p>
    </header>

    <section class="content-section author-detail__works">
      <div class="section-heading">
        <div>
          <p class="section-kicker">已发布作品</p>
          <h2>{{ author.poem_count }} 首</h2>
        </div>
      </div>
      <div v-if="author.poems.length" class="author-poem-list">
        <RouterLink
          v-for="poem in author.poems"
          :key="poem.id"
          class="author-poem"
          :to="`/poems/${poem.id}`"
        >
          <strong>{{ poem.title }}</strong>
          <span>{{ poem.summary || '查看作品正文' }}</span>
        </RouterLink>
      </div>
      <div v-else class="empty-state">
        <strong>暂无已发布作品</strong>
      </div>
    </section>
  </div>

  <div v-else class="page empty-state">
    <strong>暂时无法读取作者</strong>
    <p>{{ errorMessage }}</p>
    <button class="text-button" type="button" @click="loadAuthor">重试</button>
  </div>
</template>