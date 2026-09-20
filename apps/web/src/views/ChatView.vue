<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import {
  chatApi,
  type ChatRetrievalEvent,
  type ChatStreamErrorEvent,
} from '@/api/chat'
import { ApiClientError, getErrorMessage } from '@/api/http'
import type { ChatMessage, Conversation } from '@/types/api'

interface LocalChatMessage extends ChatMessage {
  retrieval?: ChatRetrievalEvent
}

const route = useRoute()
const router = useRouter()
const conversations = ref<Conversation[]>([])
const messages = ref<LocalChatMessage[]>([])
const draft = ref('')
const isLoadingConversations = ref(true)
const isLoadingConversation = ref(false)
const isStreaming = ref(false)
const errorMessage = ref('')
const threadScroll = ref<HTMLElement | null>(null)
const abortController = ref<AbortController | null>(null)

const suggestions = [
  '李白写过哪些月亮意象的诗？',
  '苏轼关于中秋的词有哪些？',
  '想家的诗常用哪些意象？',
]

const activeConversationId = computed<number | null>(() => {
  const raw = route.params.conversationId
  const value = Array.isArray(raw) ? raw[0] : raw
  const id = Number(value)
  return Number.isInteger(id) && id > 0 ? id : null
})

const canSend = computed(
  () =>
    draft.value.trim().length > 0 &&
    !isStreaming.value &&
    !isLoadingConversation.value,
)

let conversationLoadGeneration = 0
let skipConversationLoad: number | null = null
let temporaryId = -1

async function loadConversations(): Promise<void> {
  isLoadingConversations.value = true
  try {
    conversations.value = await chatApi.listConversations()
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
  } finally {
    isLoadingConversations.value = false
  }
}

async function loadConversation(conversationId: number | null): Promise<void> {
  const generation = ++conversationLoadGeneration
  errorMessage.value = ''

  if (conversationId === null) {
    messages.value = []
    isLoadingConversation.value = false
    return
  }

  isLoadingConversation.value = true
  try {
    const loaded = await chatApi.listMessages(conversationId)
    if (generation !== conversationLoadGeneration) {
      return
    }
    messages.value = loaded.map((message) => ({ ...message }))
    await scrollToBottom()
  } catch (error) {
    if (generation !== conversationLoadGeneration) {
      return
    }
    messages.value = []
    if (
      error instanceof ApiClientError &&
      error.code === 'CONVERSATION_NOT_FOUND'
    ) {
      ElMessage.error(error.message)
      await router.replace({ name: 'chat-home' })
      return
    }
    errorMessage.value = getErrorMessage(error)
  } finally {
    if (generation === conversationLoadGeneration) {
      isLoadingConversation.value = false
    }
  }
}

watch(
  activeConversationId,
  async (conversationId) => {
    if (conversationId !== null && skipConversationLoad === conversationId) {
      skipConversationLoad = null
      return
    }
    await loadConversation(conversationId)
  },
  { immediate: true },
)

watch(
  () => {
    const last = messages.value[messages.value.length - 1]
    return [
      messages.value.length,
      last?.content.length ?? 0,
      last?.citations.length ?? 0,
      last?.status ?? '',
      last?.retrieval?.selected_count ?? '',
    ].join(':')
  },
  () => {
    void scrollToBottom()
  },
)

onMounted(() => {
  void loadConversations()
})

async function ensureConversation(content: string): Promise<number> {
  if (activeConversationId.value !== null) {
    return activeConversationId.value
  }

  const conversation = await chatApi.createConversation({
    title: makeConversationTitle(content),
  })
  conversations.value = [
    conversation,
    ...conversations.value.filter((item) => item.id !== conversation.id),
  ]
  skipConversationLoad = conversation.id
  await router.replace({
    name: 'chat-detail',
    params: { conversationId: String(conversation.id) },
  })
  return conversation.id
}

async function sendMessage(): Promise<void> {
  const content = draft.value.trim()
  if (!content || isStreaming.value || isLoadingConversation.value) {
    return
  }

  errorMessage.value = ''
  let conversationId: number
  try {
    conversationId = await ensureConversation(content)
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
    return
  }

  draft.value = ''
  const timestamp = new Date().toISOString()
  const userMessage: LocalChatMessage = {
    id: temporaryId--,
    conversation_id: conversationId,
    role: 'user',
    content,
    status: 'completed',
    model: null,
    latency_ms: null,
    error_code: null,
    citations: [],
    created_at: timestamp,
    updated_at: timestamp,
  }
  const assistantMessage: LocalChatMessage = {
    id: temporaryId--,
    conversation_id: conversationId,
    role: 'assistant',
    content: '',
    status: 'streaming',
    model: null,
    latency_ms: null,
    error_code: null,
    citations: [],
    created_at: timestamp,
    updated_at: timestamp,
  }
  messages.value.push(userMessage, assistantMessage)
  await scrollToBottom()

  const controller = new AbortController()
  abortController.value = controller
  isStreaming.value = true
  try {
    await chatApi.streamMessage(
      conversationId,
      content,
      {
        onMeta(event) {
          assistantMessage.id = event.message_id
        },
        onRetrieval(event) {
          assistantMessage.retrieval = event
        },
        onDelta(event) {
          assistantMessage.content += event.text
        },
        onCitation(citation) {
          const exists = assistantMessage.citations.some(
            (item) =>
              item.chunk_id === citation.chunk_id &&
              item.rank === citation.rank,
          )
          if (!exists) {
            assistantMessage.citations.push({
              ...citation,
              id: temporaryId--,
            })
          }
        },
        onDone(event) {
          assistantMessage.status = 'completed'
          assistantMessage.latency_ms = event.latency_ms
        },
        onError(event) {
          applyStreamError(assistantMessage, event)
        },
      },
      controller.signal,
    )
  } catch (error) {
    if (isAbortError(error)) {
      assistantMessage.status = 'cancelled'
    } else {
      assistantMessage.status = 'failed'
      if (!assistantMessage.content) {
        assistantMessage.content = '回答生成失败，请重试。'
      }
      errorMessage.value = getErrorMessage(error)
    }
  } finally {
    isStreaming.value = false
    abortController.value = null
    await loadConversations()
    await scrollToBottom()
  }
}

function applyStreamError(
  message: LocalChatMessage,
  event: ChatStreamErrorEvent,
): void {
  message.status = 'failed'
  message.error_code = event.code
  errorMessage.value = event.message
}

function stopGenerating(): void {
  abortController.value?.abort()
}

async function newConversation(): Promise<void> {
  if (isStreaming.value) {
    return
  }
  draft.value = ''
  errorMessage.value = ''
  await router.push({ name: 'chat-home' })
}

async function selectConversation(conversation: Conversation): Promise<void> {
  if (isStreaming.value || conversation.id === activeConversationId.value) {
    return
  }
  errorMessage.value = ''
  await router.push({
    name: 'chat-detail',
    params: { conversationId: String(conversation.id) },
  })
}

async function removeConversation(conversation: Conversation): Promise<void> {
  if (isStreaming.value) {
    return
  }
  if (!window.confirm(`删除会话“${conversation.title}”？`)) {
    return
  }

  try {
    await chatApi.deleteConversation(conversation.id)
    conversations.value = conversations.value.filter(
      (item) => item.id !== conversation.id,
    )
    if (conversation.id === activeConversationId.value) {
      await router.replace({ name: 'chat-home' })
    }
    ElMessage.success('会话已删除')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

function useSuggestion(value: string): void {
  draft.value = value
  void sendMessage()
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const element = threadScroll.value
  if (element) {
    element.scrollTop = element.scrollHeight
  }
}

function makeConversationTitle(content: string): string {
  const normalized = content.replace(/\s+/g, ' ').trim()
  return normalized.length > 24 ? `${normalized.slice(0, 24)}…` : normalized
}

function formatConversationTime(conversation: Conversation): string {
  const value = conversation.last_message_at ?? conversation.updated_at
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return ''
  }
  return timestampFormatter.format(date)
}

function granularityLabel(granularity: string): string {
  if (granularity === 'line') {
    return '诗句'
  }
  if (granularity === 'poem') {
    return '全诗'
  }
  if (granularity === 'note') {
    return '注释'
  }
  return '依据'
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

const timestampFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric',
  day: 'numeric',
})
</script>

<template>
  <div class="page chat-page">
    <header class="page-heading page-heading--compact chat-page__heading">
      <div>
        <p class="section-kicker">诗词知识问答</p>
        <h1>问诗</h1>
      </div>
      <p>回答只依据诗词库中的诗句、注释和目录信息，并保留可追溯引用。</p>
    </header>

    <div class="chat-workspace">
      <aside class="chat-rail" aria-label="会话记录">
        <div class="chat-rail__header">
          <h2>会话</h2>
          <button
            class="text-button"
            type="button"
            :disabled="isStreaming"
            @click="newConversation"
          >
            新会话
          </button>
        </div>

        <p v-if="isLoadingConversations" class="chat-rail__state">正在读取会话</p>
        <p v-else-if="!conversations.length" class="chat-rail__state">
          还没有问答记录
        </p>
        <ul v-else class="chat-conversation-list">
          <li
            v-for="conversation in conversations"
            :key="conversation.id"
            class="chat-conversation"
            :class="{
              'chat-conversation--active':
                conversation.id === activeConversationId,
            }"
          >
            <button
              class="chat-conversation__select"
              type="button"
              :aria-current="
                conversation.id === activeConversationId ? 'page' : undefined
              "
              :disabled="isStreaming && conversation.id !== activeConversationId"
              @click="selectConversation(conversation)"
            >
              <span>{{ conversation.title }}</span>
              <time :datetime="conversation.last_message_at ?? conversation.updated_at">
                {{ formatConversationTime(conversation) }}
              </time>
            </button>
            <button
              class="chat-conversation__remove"
              type="button"
              :aria-label="`删除会话：${conversation.title}`"
              :disabled="isStreaming"
              @click="removeConversation(conversation)"
            >
              删除
            </button>
          </li>
        </ul>
      </aside>

      <section class="chat-thread" aria-label="当前问答">
        <div ref="threadScroll" class="chat-thread__scroll">
          <div v-if="isLoadingConversation" class="chat-thread__state">
            <strong>正在读取问答记录</strong>
          </div>

          <div v-else-if="!messages.length" class="chat-empty">
            <p class="section-kicker">从一首诗开始</p>
            <h2>想了解哪个意象、作者或作品？</h2>
            <p>可以问出处、意境、背景，也可以比较相近的诗词表达。</p>
            <div class="chat-suggestions">
              <button
                v-for="suggestion in suggestions"
                :key="suggestion"
                type="button"
                @click="useSuggestion(suggestion)"
              >
                {{ suggestion }}
              </button>
            </div>
            <p v-if="errorMessage" class="form-error" role="alert">
              {{ errorMessage }}
            </p>
          </div>

          <div v-else class="chat-messages">
            <article
              v-for="message in messages"
              :key="message.id"
              class="chat-message"
              :class="`chat-message--${message.role}`"
            >
              <header class="chat-message__header">
                <span class="chat-message__seal" aria-hidden="true">
                  {{ message.role === 'user' ? '问' : '答' }}
                </span>
                <strong>{{ message.role === 'user' ? '你' : '诗库' }}</strong>
                <span v-if="message.status === 'streaming'" class="chat-message__state">
                  正在生成
                </span>
                <span v-else-if="message.status === 'failed'" class="chat-message__state">
                  生成失败
                </span>
                <span
                  v-else-if="message.status === 'cancelled'"
                  class="chat-message__state"
                >
                  已停止
                </span>
              </header>

              <p class="chat-message__content">
                {{
                  message.content ||
                  (message.status === 'streaming' ? '正在检索诗词依据…' : '')
                }}
              </p>

              <p
                v-if="message.role === 'assistant' && message.retrieval"
                class="chat-retrieval"
              >
                <template v-if="message.retrieval.selected_count > 0">
                  已从 {{ message.retrieval.candidate_count }} 条候选中选取
                  {{ message.retrieval.selected_count }} 条依据
                </template>
                <template v-else>没有找到足够依据，本次不生成推测性回答。</template>
              </p>

              <div v-if="message.citations.length" class="chat-citations">
                <article
                  v-for="citation in message.citations"
                  :key="`${citation.rank}-${citation.chunk_id ?? citation.id}`"
                  class="chat-citation"
                >
                  <header>
                    <RouterLink
                      v-if="citation.poem_id"
                      :to="`/poems/${citation.poem_id}`"
                    >
                      {{ citation.title }}
                    </RouterLink>
                    <strong v-else>{{ citation.title }}</strong>
                    <span>{{ granularityLabel(citation.granularity) }}</span>
                  </header>
                  <p class="chat-citation__meta">
                    <span v-if="citation.dynasty_name">{{ citation.dynasty_name }}</span>
                    <span v-if="citation.author_name">{{ citation.author_name }}</span>
                  </p>
                  <blockquote>{{ citation.text }}</blockquote>
                </article>
              </div>
            </article>
          </div>
        </div>

        <form class="chat-composer" @submit.prevent="sendMessage">
          <label class="sr-only" for="chat-question">输入诗词问题</label>
          <textarea
            id="chat-question"
            v-model="draft"
            rows="3"
            maxlength="2000"
            placeholder="写下要查证或理解的问题"
            :disabled="isLoadingConversation"
            @keydown.enter.exact.prevent="sendMessage"
          />
          <div class="chat-composer__footer">
            <p v-if="errorMessage" class="form-error" role="alert">
              {{ errorMessage }}
            </p>
            <span v-else class="chat-composer__count">
              {{ draft.length }} / 2000
            </span>
            <button
              v-if="isStreaming"
              class="button button--secondary"
              type="button"
              @click="stopGenerating"
            >
              停止生成
            </button>
            <button v-else class="button button--primary" type="submit" :disabled="!canSend">
              发送
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  padding-top: 42px;
}

.chat-page__heading {
  margin-bottom: 22px;
}

.chat-workspace {
  display: grid;
  min-height: min(720px, calc(100vh - 250px));
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--white);
  grid-template-columns: 248px minmax(0, 1fr);
}

.chat-rail {
  min-width: 0;
  border-right: 1px solid var(--line);
  background:
    linear-gradient(90deg, rgba(33, 106, 89, 0.035) 1px, transparent 1px) 0 0 / 28px 100%,
    #efeee7;
}

.chat-rail__header {
  display: flex;
  min-height: 68px;
  padding: 0 18px 0 20px;
  border-bottom: 1px solid var(--line);
  align-items: center;
  justify-content: space-between;
}

.chat-rail__header h2 {
  margin: 0;
  font-size: 20px;
}

.chat-rail__header .text-button {
  font-size: 13px;
}

.chat-rail__state {
  margin: 0;
  padding: 24px 20px;
  color: var(--ink-soft);
  font-size: 13px;
}

.chat-conversation-list {
  max-height: 670px;
  margin: 0;
  padding: 8px 0;
  overflow-y: auto;
  list-style: none;
}

.chat-conversation {
  display: grid;
  margin: 0 8px;
  border-bottom: 1px solid rgba(201, 206, 201, 0.72);
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
}

.chat-conversation--active {
  border-bottom-color: rgba(33, 106, 89, 0.24);
  background: rgba(255, 254, 249, 0.78);
  box-shadow: inset 3px 0 var(--jade);
}

.chat-conversation__select {
  display: block;
  min-width: 0;
  padding: 14px 8px 14px 12px;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.chat-conversation__select span,
.chat-conversation__select time {
  display: block;
}

.chat-conversation__select span {
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-conversation__select time {
  margin-top: 5px;
  color: var(--ink-soft);
  font-size: 11px;
}

.chat-conversation__select:hover:not(:disabled) span {
  color: var(--jade-deep);
}

.chat-conversation__remove {
  padding: 8px 6px;
  color: var(--ink-soft);
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
}

.chat-conversation__remove:hover:not(:disabled) {
  color: var(--cinnabar);
}

.chat-conversation__select:disabled,
.chat-conversation__remove:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.chat-thread {
  display: grid;
  min-width: 0;
  min-height: 0;
  grid-template-rows: minmax(0, 1fr) auto;
}

.chat-thread__scroll {
  min-height: 0;
  overflow-y: auto;
}

.chat-thread__state {
  display: grid;
  min-height: 420px;
  color: var(--ink-soft);
  place-items: center;
}

.chat-empty {
  display: flex;
  min-height: 520px;
  max-width: 620px;
  margin: 0 auto;
  padding: 64px 36px 48px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}

.chat-empty h2 {
  max-width: 540px;
  margin-bottom: 14px;
  font-size: clamp(26px, 3.5vw, 38px);
  line-height: 1.35;
}

.chat-empty > p:not(.section-kicker, .form-error) {
  max-width: 470px;
  color: var(--ink-soft);
  line-height: 1.8;
}

.chat-suggestions {
  display: grid;
  width: 100%;
  margin-top: 28px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.chat-suggestions button {
  min-height: 96px;
  padding: 14px;
  color: var(--ink-soft);
  border: 1px solid var(--line);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  line-height: 1.65;
  text-align: left;
}

.chat-suggestions button:hover {
  color: var(--jade-deep);
  border-color: rgba(33, 106, 89, 0.55);
  background: rgba(33, 106, 89, 0.04);
}

.chat-messages {
  padding: 8px 44px 48px;
}

.chat-message {
  padding: 30px 0 34px;
  border-bottom: 1px solid var(--line);
}

.chat-message:last-child {
  border-bottom: 0;
}

.chat-message--user {
  margin-right: 14%;
}

.chat-message--assistant {
  margin-left: calc(18px + 4%);
  border-left: 3px solid var(--cinnabar);
  padding-left: 24px;
}

.chat-message__header {
  display: flex;
  margin-bottom: 16px;
  align-items: center;
  gap: 9px;
}

.chat-message__header strong {
  font-family: "Noto Serif SC", "Songti SC", serif;
}

.chat-message__seal {
  display: grid;
  width: 28px;
  height: 28px;
  color: var(--white);
  border-radius: 3px;
  background: var(--ink);
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 14px;
  place-items: center;
}

.chat-message--assistant .chat-message__seal {
  background: var(--cinnabar);
}

.chat-message__state {
  margin-left: auto;
  color: var(--ink-soft);
  font-size: 12px;
}

.chat-message__content {
  margin-bottom: 0;
  color: var(--ink);
  font-family:
    "Noto Serif SC", "Source Han Serif SC", "Songti SC", SimSun, serif;
  font-size: 17px;
  line-height: 2;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-message--user .chat-message__content {
  color: var(--ink-soft);
  font-family:
    "Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", sans-serif;
  font-size: 15px;
  line-height: 1.85;
}

.chat-retrieval {
  margin: 20px 0 0;
  padding: 10px 13px;
  color: var(--jade-deep);
  border: 1px solid rgba(33, 106, 89, 0.2);
  border-radius: 3px;
  background: rgba(33, 106, 89, 0.045);
  font-size: 12px;
}

.chat-citations {
  display: grid;
  margin-top: 24px;
  gap: 12px;
}

.chat-citation {
  padding: 17px 18px 16px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: #f7f6f0;
}

.chat-citation header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.chat-citation header a,
.chat-citation header strong {
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 16px;
  font-weight: 650;
}

.chat-citation header a:hover {
  color: var(--jade);
}

.chat-citation header span {
  flex: 0 0 auto;
  color: var(--cinnabar);
  font-size: 11px;
}

.chat-citation__meta {
  display: flex;
  margin: 6px 0 10px;
  color: var(--ink-soft);
  font-size: 12px;
  gap: 12px;
}

.chat-citation__meta span + span::before {
  margin-right: 12px;
  color: var(--cinnabar);
  content: "·";
}

.chat-citation blockquote {
  margin: 0;
  color: var(--ink-soft);
  font-family:
    "Noto Serif SC", "Source Han Serif SC", "Songti SC", SimSun, serif;
  font-size: 14px;
  line-height: 1.85;
  white-space: pre-wrap;
}

.chat-composer {
  padding: 16px 22px 20px;
  border-top: 1px solid var(--line);
  background: #f4f3ed;
}

.chat-composer textarea {
  display: block;
  width: 100%;
  min-height: 82px;
  max-height: 180px;
  padding: 13px 15px;
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: 4px;
  outline: none;
  background: var(--white);
  line-height: 1.65;
  resize: vertical;
}

.chat-composer textarea:focus {
  border-color: var(--jade);
  box-shadow: 0 0 0 3px rgba(33, 106, 89, 0.12);
}

.chat-composer textarea:disabled {
  background: var(--paper-deep);
}

.chat-composer__footer {
  display: flex;
  min-height: 38px;
  margin-top: 10px;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
}

.chat-composer__footer .form-error {
  margin: 0 auto 0 0;
  padding: 7px 10px;
  font-size: 12px;
}

.chat-composer__count {
  margin-right: auto;
  color: var(--ink-soft);
  font-size: 12px;
}

@media (max-width: 820px) {
  .chat-page {
    width: min(calc(100% - 28px), var(--content-width));
    padding-top: 30px;
  }

  .chat-workspace {
    min-height: 0;
    grid-template-columns: 1fr;
  }

  .chat-rail {
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .chat-rail__header {
    min-height: 58px;
  }

  .chat-conversation-list {
    display: flex;
    max-height: none;
    padding: 8px;
    overflow-x: auto;
    gap: 8px;
  }

  .chat-conversation {
    flex: 0 0 210px;
    margin: 0;
    border: 1px solid var(--line);
    border-radius: 4px;
  }

  .chat-conversation--active {
    box-shadow: inset 0 -3px var(--jade);
  }

  .chat-thread__scroll {
    overflow: visible;
  }

  .chat-empty {
    min-height: 430px;
    padding-inline: 22px;
  }

  .chat-suggestions {
    grid-template-columns: 1fr;
  }

  .chat-suggestions button {
    min-height: 0;
  }

  .chat-messages {
    padding: 0 18px 36px;
  }

  .chat-message--user,
  .chat-message--assistant {
    margin-right: 0;
    margin-left: 0;
  }

  .chat-message--assistant {
    padding-left: 16px;
  }

  .chat-composer {
    padding: 14px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .chat-conversation__select:hover:not(:disabled) span,
  .chat-conversation__remove:hover:not(:disabled) {
    transition: none;
  }
}
</style>
