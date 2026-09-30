<script setup>
import { ref, onMounted, watch, nextTick } from "vue";
import Sidebar from "./components/Sidebar.vue";
import MessageBubble from "./components/MessageBubble.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import Welcome from "./components/Welcome.vue";
import AuthForm from "./components/AuthForm.vue";
import { sendReview, listSessions, getHistory, removeSession, uploadFile, truncateHistory, regenerateChat, me, getToken, setToken, clearToken } from "./api.js";

const user = ref(null); // 登录用户；null = 未登录，显示登录页
const sessionId = ref(crypto.randomUUID());
const messages = ref([]); // {id, role, content, trace?, citation_check?}
const sessions = ref([]);
const input = ref("");
const sending = ref(false);
const uploading = ref(false);
const fileInput = ref(null);
const uploadedFile = ref(null); // {name, text?}；历史附件只保留名称，全文仍在服务端
const clearContractOnNext = ref(false); // 显式移除附件后，下一条请求才传 contract: null
const pendingDelete = ref(null); // 待删除的 session_id，非空则弹确认框
const editingId = ref(null); // 正在修改的用户消息 id，非空则其气泡进入编辑态

async function loadSessions() {
  try {
    sessions.value = (await listSessions()).sessions;
  } catch (e) {
    sessions.value = [];
  }
}

async function openSession(sid) {
  const data = await getHistory(sid);
  sessionId.value = sid;
  messages.value = data.history;
  uploadedFile.value = data.contract ? { name: data.contract.name } : null;
  clearContractOnNext.value = false;
  editingId.value = null;
  loadSessions();
}

function askDelete(sid) {
  pendingDelete.value = sid;
}

function cancelDelete() {
  pendingDelete.value = null;
}

async function doDelete() {
  const sid = pendingDelete.value;
  pendingDelete.value = null;
  if (!sid) return;
  await removeSession(sid);
  if (sid === sessionId.value) {
    sessionId.value = crypto.randomUUID();
    messages.value = [];
  }
  loadSessions();
}

function newChat() {
  // 只开一个全新的空会话，不删当前会话（保留在侧栏可点回）
  sessionId.value = crypto.randomUUID();
  messages.value = [];
  uploadedFile.value = null;
  clearContractOnNext.value = false;
  editingId.value = null;
  loadSessions();
}

// 点欢迎区快捷问题：填入输入框并直接发送
function ask(q) {
  input.value = q;
  submit();
}

// 点"核查合同"入口：填入引导语但不自动发送，让用户粘贴合同全文后再发
function review() {
  input.value = "请核查这份合同中的风险条款，并为每个结论检索法律依据：\n\n";
}

function pickFile() {
  fileInput.value?.click();
}

// 上传合同文件：后端抽成纯文本挂到"已附加"标签，不进输入框（不占用户提问的位置）
async function onFile(e) {
  const file = e.target.files?.[0];
  e.target.value = ""; // 允许连续选同一个文件
  if (!file) return;
  uploading.value = true;
  try {
    const data = await uploadFile(file);
    uploadedFile.value = { name: data.filename, text: data.text };
    clearContractOnNext.value = false;
  } catch (err) {
    messages.value.push({ role: "assistant", content: `上传失败：${err.message}` });
  } finally {
    uploading.value = false;
  }
}

// 移除已附加的合同文件（下次发送不再携带）
function removeUpload() {
  clearContractOnNext.value = !!uploadedFile.value;
  uploadedFile.value = null;
}

async function submit() {
  if (sending.value || uploading.value) return;
  const q = input.value.trim();
  // 新上传文本才随请求发送；已持久化的附件省略字段即可由服务端保留。
  const contract = uploadedFile.value
    ? (uploadedFile.value.text ?? undefined)
    : (clearContractOnNext.value ? null : undefined);
  // 合同走独立 contract 参数发给 agent；输入框只发用户问题（或默认审查指令）
  const msg = q || (uploadedFile.value ? "请核查这份合同中的风险条款，并为每个结论检索法律依据" : "");
  if (!msg) return;
  input.value = "";
  const sent = await sendAndAppend(msg, contract);
  if (sent && contract === null) clearContractOnNext.value = false;
}

// 真正发一条消息并追加到气泡：成功则带后端返回的消息 id（供后续修改/重新生成定位）
// 带合同时，用户气泡展示「合同全文 + 用户输入」；但发给后端仍是单独的 msg（问题）
// + contract（独立参数），agent 上下文里合同只注入一次，不破坏"合同 vs 问题"的分离。
const CONTRACT_PREFIX = "待审查合同全文：\n\n";
function stripContract(display, contract) {
  const pre = CONTRACT_PREFIX + (contract ?? "") + "\n\n";
  return display.startsWith(pre) ? display.slice(pre.length) : display;
}

async function sendAndAppend(msg, contract) {
  if (sending.value) return;
  const display = contract ? CONTRACT_PREFIX + contract + "\n\n" + msg : msg;
  const userMsg = { role: "user", content: display };
  messages.value.push(userMsg);
  sending.value = true;
  try {
    const contractName = contract && uploadedFile.value?.text ? uploadedFile.value.name : undefined;
    const data = await sendReview(msg, sessionId.value, contract, contractName);
    userMsg.id = data.user_id;
    messages.value.push({
      id: data.assistant_id,
      role: "assistant",
      content: data.answer,
      trace: data.trace,
      citation_check: data.citation_check,
    });
    loadSessions();
    return true;
  } catch (err) {
    messages.value.push({ role: "assistant", content: `请求失败：${err.message}` });
    return false;
  } finally {
    sending.value = false;
  }
}

// 进入修改模式：定位用户消息，其气泡切换成可编辑
function onEdit(msg) {
  editingId.value = msg.id;
}

function onEditCancel() {
  editingId.value = null;
}

// 修改并重发：截断到该条用户消息之前，删除其后所有消息，再以新文本重发一轮
async function onEditSubmit(msg, newText) {
  const i = messages.value.findIndex((m) => m.id === msg.id);
  if (i < 0 || !newText) return;
  editingId.value = null;
  messages.value = messages.value.slice(0, i); // 丢掉本条及其后
  try {
    await truncateHistory(sessionId.value, msg.id);
    // 修改重发也要带上当前附加的合同，否则会把会话里已存的合同清掉；
    // 编辑框里是「合同全文+问题」的展示文本，需先剥掉合同前缀，避免重复拼接
    const contractText = uploadedFile.value
      ? (uploadedFile.value.text ?? undefined)
      : (clearContractOnNext.value ? null : undefined);
    const sent = await sendAndAppend(stripContract(newText, contractText), contractText);
    if (sent && contractText === null) clearContractOnNext.value = false;
  } catch (err) {
    messages.value.push({ role: "assistant", content: `请求失败：${err.message}` });
  }
}

// 重新生成：后端删掉最后一条回答重跑 agent，用新回答原位替换
async function onRegenerate(msg) {
  const i = messages.value.findIndex((m) => m.id === msg.id);
  if (i < 0) return;
  sending.value = true;
  try {
    const data = await regenerateChat(sessionId.value);
    messages.value[i] = {
      id: data.assistant_id,
      role: "assistant",
      content: data.answer,
      trace: data.trace,
      citation_check: data.citation_check,
    };
  } catch (err) {
    messages.value.push({ role: "assistant", content: `请求失败：${err.message}` });
  } finally {
    sending.value = false;
  }
}

// ---- 登录态 ----
function handleAuthSuccess(data) {
  setToken(data.token);
  user.value = { username: data.username };
  sessionId.value = crypto.randomUUID();
  messages.value = [];
  uploadedFile.value = null;
  clearContractOnNext.value = false;
  loadSessions();
}

function logout() {
  clearToken();
  user.value = null;
  messages.value = [];
  sessions.value = [];
  sessionId.value = crypto.randomUUID();
  uploadedFile.value = null;
  clearContractOnNext.value = false;
}

onMounted(async () => {
  // 有 token 先验有效性：有效则直接进主界面，失效则清 token 停在登录页
  if (getToken()) {
    try {
      user.value = await me();
      await loadSessions();
    } catch {
      clearToken();
    }
  }
});

// 每次新消息加入后自动滚到对话底部，免手动滑动
async function scrollToBottom() {
  await nextTick();
  const el = document.getElementById("messages");
  if (el) el.scrollTop = el.scrollHeight;
}
watch(() => messages.value.length, scrollToBottom);
</script>

<template>
  <AuthForm v-if="!user" @success="handleAuthSuccess" />
  <div v-else class="layout">
    <Sidebar
      :sessions="sessions"
      :active-id="sessionId"
      :username="user.username"
      @open="openSession"
      @new="newChat"
      @delete="askDelete"
      @logout="logout"
    />

    <main>
      <div id="messages" aria-live="polite">
        <Welcome v-if="!messages.length" @ask="ask" @review="review" />
        <template v-else>
          <MessageBubble
            v-for="(m, i) in messages"
            :key="m.id ?? i"
            :msg="m"
            :editing="m.id === editingId"
            @edit="onEdit"
            @edit-submit="onEditSubmit"
            @edit-cancel="onEditCancel"
            @regenerate="onRegenerate"
          />
        </template>
      </div>

      <form id="chat-form" @submit.prevent="submit">
        <div class="chat-toolbar">
          <button type="button" id="upload-btn" @click="pickFile" :disabled="sending || uploading">
            {{ uploading ? "解析中…" : "⬆ 上传合同" }}
          </button>
          <span v-if="uploadedFile" class="upload-chip" title="点击 ✕ 移除，发送时会把此合同全文带给助手">
            📄 {{ uploadedFile.name }}
            <button type="button" class="chip-x" @click="removeUpload" aria-label="移除附件">✕</button>
          </span>
        </div>
        <div class="chat-input-row">
          <textarea
            v-model="input"
            rows="2"
            placeholder="上传合同后在此输入你的问题；或直接输入问题/粘贴合同"
            @keydown.enter.exact.prevent="submit"
          ></textarea>
          <button type="submit" id="send-btn" :disabled="sending || uploading">
            {{ sending ? "思考中…" : "发送" }}
          </button>
        </div>
        <input ref="fileInput" type="file" accept=".docx,.pdf,.txt,.md" hidden @change="onFile" />
      </form>
      <p class="form-hint">上传 .docx/.pdf/.txt 合同自动附加 · Enter 发送 · Shift+Enter 换行 · 内容基于检索到的法条，请以官方文本为准</p>
    </main>
  </div>

  <ConfirmDialog
    :visible="!!pendingDelete"
    message="确定删除这个会话吗？此操作不可恢复。"
    @confirm="doDelete"
    @cancel="cancelDelete"
  />
</template>
