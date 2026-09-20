<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PoemListItem from '@/components/PoemListItem.vue'
import { poemsApi } from '@/api/poems'
import { getErrorMessage } from '@/api/http'
import type { Poem } from '@/types/api'

const router = useRouter()
const query = ref('')
const poems = ref<Poem[]>([])
const isLoading = ref(true)
const errorMessage = ref('')

async function search(): Promise<void> {
  await router.push({
    name: 'search',
    query: query.value.trim() ? { q: query.value.trim() } : {},
  })
}

async function loadLatest(): Promise<void> {
  isLoading.value = true
  errorMessage.value = ''
  try {
    const result = await poemsApi.list({ page: 1, page_size: 4 })
    poems.value = result.items
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  void loadLatest()
})
</script>

<template>
  <div class="page">
    <section class="search-band">
      <div class="search-band__copy">
        <p class="section-kicker">诗词检索</p>
        <h1>从一句诗，找到它的来处</h1>
        <p>输入诗句、作者、朝代、体裁或主题。</p>
      </div>
      <form class="search-form" role="search" @submit.prevent="search">
        <label class="sr-only" for="home-search">搜索诗词</label>
        <input
          id="home-search"
          v-model="query"
          type="search"
          placeholder="例如：明月、李白、宋词、思乡"
          autocomplete="off"
        />
        <button type="submit">检索</button>
      </form>
    </section>

    <section class="content-section">
      <header class="section-heading">
        <div>
          <p class="section-kicker">近期收录</p>
          <h2>一篇一境</h2>
        </div>
        <RouterLink class="text-link" to="/poems">查看全部</RouterLink>
      </header>

      <div v-if="poems.length" class="poem-grid">
        <PoemListItem v-for="poem in poems" :key="poem.id" :poem="poem" />
      </div>
      <div v-else-if="isLoading" class="empty-state">
        <strong>正在读取诗词</strong>
      </div>
      <div v-else-if="errorMessage" class="empty-state">
        <strong>暂时无法读取诗词</strong>
        <p>{{ errorMessage }}</p>
        <button class="text-button" type="button" @click="loadLatest">重新加载</button>
      </div>
      <div v-else class="empty-state">
        <strong>诗词库还是空的</strong>
        <p>管理员发布作品后，这里会显示最新收录。</p>
      </div>
    </section>

    <section class="reading-note">
      <p>“诗者，志之所之也。在心为志，发言为诗。”</p>
      <span>《毛诗序》</span>
    </section>
  </div>
</template>