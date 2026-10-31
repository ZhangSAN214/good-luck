// 页面控制：状态、事件流、交互。渲染在 view.js，通信在 api.js。
import * as API from './api.js';
import {
  STATUS_LABELS,
  MAKE_TARGETS,
  MEDIA_LABELS,
  STEP_LABELS,
  attachmentsHTML,
  estimateHTML,
  previewHTML,
  splitPanel,
  toolsPanel,
  COORD_STEPS,
  castPanel,
  contributionsPanel,
  officePanel,
  channelsPanel,
  pluginBoardPanel,
  directoryPanel,
  directoryList,
  directorySnippet,
  esc,
  feedItems,
  flowPanel,
  historyPanel,
  legendHTML,
  mediaPanel,
  money,
  reviewsPanel,
  stageHTML,
  setKindLabels,
  setMemberArt,
  usagePanel,
} from './view.js';
import { Pet } from './agent/pet.js';
import { Apartment } from './apartment.js';
import { GroupChat } from './groupchat.js';
import { People } from './people.js';
import { Feeds } from './feeds.js';
import { Clipper } from './clipper.js';
import { Exporter, exportHref } from './exporter.js';
import { Moments } from './moments.js';
import { BootSettings } from './bootcfg.js';
import { LanPanel } from './lan.js';
import { BackupPanel } from './backup.js';
import { Watch } from './watch.js';
import { Wander } from './wander.js';
import { Workbench } from './workbench.js';
import { SuperPanel } from './supercomputer.js';
import { Bench } from './bench.js';
import { BenchGenPanel } from './benchgen.js';
import { Hub } from './hub.js';
import { Standee } from './standee.js';
import { Music } from './music.js';
import { Clock } from './clock.js';
import { Notice } from './notice.js';
import { Mail } from './mail.js';
import { Evolution } from './evolution.js';
import { Usage } from './usage.js';
import { Deck } from './deck.js';
import { setMemberPacks } from './stickers.js';

const $ = (q) => document.querySelector(q);
const RESTING = new Set(['completed', 'stopped', 'failed', 'awaiting_confirmation', 'paused']);
const deck = new Deck(); // 圆桌中央的花切转场

// 作答方式：每位使用者在本机记住上次的选择（只是便利，读不到就用默认的办公室模式）
const WORKFLOW_KEY = 'roundtable.workflow';
function savedWorkflow() {
  try {
    return localStorage.getItem(WORKFLOW_KEY);
  } catch {
    return null;
  }
}

const S = {
  carry: null, // 接力：{ sid, question, summary, workflow, model }，提交时带上上一场的成果摘要
  status: null, // /api/status
  budget: null,
  history: null,
  contribHistory: null,
  office: null, // 员工名册（/api/office）
  board: null, // 插件榜（/api/plugins/board）：只读
  boardFilter: 'all',
  talent: null, // 人才市场（/api/models/candidates）：只提示，不改配置
  directory: null, // 模型图鉴（/api/models/directory）：只浏览，不上桌、不改配置
  dirFilter: { cat: 'text', tag: '', q: '', sort: 'new', limit: 80 },
  payslip: null, // { sid, data }：当前会话的工资单
  sid: null,
  session: null,
  tab: 'flow',
  tier: null, // 档位名或 custom
  workflow: savedWorkflow() || 'office', // 记住上次选的方式；office 办公室（经理派一位员工办理，默认）/ discussion 讨论 / collab 协同 / solo 个人作答 / auto 自动（分诊小组先给建议）
  anonymous: false,
  media: null, // 输出类型：null 文字 / image / speech / video / music（仅讨论模式）
  mediaTier: 'budget', // 媒体模型档位
  live: freshLive(),
  stream: null, // AbortController
  cardError: null,
  busy: false,
  attachments: [], // 已上传 / 上传中的附件（提问区）
  estimate: null, // /api/estimate 的结果
  estKey: '', // 该结果对应的请求（不含 seed）
  estSeed: null, // 提交时带上，阵容与预估一致
};

function freshLive() {
  return { table: null, step: null, act: '', speaking: new Set(), warnings: [] };
}

// 网页小人：匿名且未揭晓的会话里不出现（启动时先不显示，确定当前会话后再决定）
const pet = new Pet({ labels: STEP_LABELS, goto: gotoDiscussion });
const people = new People($('#people'), {
  goto: async (room, id) => {
    await setEntry('chat');
    await gchat.openRoom(room);
    setTimeout(() => gchat.flash(id), 300);
  },
  avatar: (id, name) => {
    const m = gchat.everyone?.find((x) => x.id === id);
    return m ? gchat.avatar(m, name) : '';
  },
  api: {
    personas: API.chatPersonas,
    setPersona: API.setChatPersona,
    voices: API.memberVoices,
    voice: API.memberVoice,
    stickers: API.memberStickers,
    drawSticker: API.drawSticker,
    delStash: API.delStash,
    clearStash: API.clearStash,
    reviewSticker: async (...args) => {
      const r = await API.reviewSticker(...args);
      loadMemberStickers();
      return r;
    },
    memories: API.chatMemories,
    addMemory: API.addChatMemory,
    editMemory: API.editChatMemory,
    delMemory: API.delChatMemory,
    clearMemories: API.clearChatMemories,
    tidy: API.tidyChatMemories,
    items: API.memoryItems,
    editItem: API.editMemoryItem,
    delItem: API.delMemoryItem,
    dropHanging: API.dropHanging,
    readDiary: API.readDiary,
    profile: API.memberProfile,
    change: API.changeMemberProfile,
    lock: API.lockMemberProfile,
    revert: API.revertMemberProfile,
    clearProfile: API.clearMemberProfile,
    importCards: API.importMemberCards,
    about: {
      get: API.aboutYou,
      add: API.aboutAdd,
      edit: API.aboutEdit,
      del: API.aboutDel,
      reject: API.aboutReject,
      lock: API.aboutLock,
      domain: API.aboutDomain,
      forget: API.aboutForget,
      clear: API.aboutClear,
      exportUrl: API.aboutExportUrl,
      status: API.aboutStatus,
      setStatus: API.aboutSetStatus,
      player: API.aboutPlayer,
      addGame: API.aboutAddGame,
      delGame: API.aboutDelGame,
      addClass: API.aboutAddClass,
      addDdl: API.aboutAddDdl,
      delSchedule: API.aboutDelSchedule,
      clearSchedule: API.aboutClearSchedule,
    },
    growth: API.memberGrowth,
    growthRun: API.runMemberGrowth,
    growthRevert: API.revertMemberGrowth,
    growthClear: API.clearMemberGrowth,
    relation: API.memberRelation,
    relationClear: API.clearMemberRelation,
    relationThink: API.thinkMemberRelation,
    relationDropView: API.dropMemberView,
    relationReplyView: API.replyMemberView,
  },
});
const feeds = new Feeds($('#feeds'), {
  api: { get: API.chatFeeds, set: API.setChatFeeds, refresh: API.refreshChatFeeds },
  memes: { get: API.chatMemes, add: API.addChatMeme, remove: API.removeChatMeme, refresh: API.refreshChatMemes },
});
const exporter = new Exporter($('#exportdlg'), { api: { options: API.exportOptions, save: API.exportSave } });
const clipper = new Clipper($('#clipdlg'), {
  api: {
    list: API.listClips,
    open: API.openClipPage,
    search: API.searchClips,
    save: API.saveClip,
    text: API.clipText,
    del: API.delClip,
    attach: API.attachClip,
  },
  attach: (att) => addClip(att),
});
const bootcfg = new BootSettings($('#bootdlg'));
$('#bootcfg').addEventListener('click', () => bootcfg.open());
new LanPanel($('#landlg'), $('#lanbtn'), {
  api: {
    status: API.lanStatus,
    setPassword: API.lanSetPassword,
    pair: API.lanPair,
    signOut: API.lanSignOut,
    logout: API.lanLogout,
  },
});
new BackupPanel($('#backupdlg'), $('#backupbtn'), {
  api: { status: API.backupStatus, checkpoint: API.backupCheckpoint, restore: API.backupRestore },
});
new Watch($('#watchdlg'), $('#watchbtn'), {
  api: {
    status: API.watchStatus,
    start: API.watchStart,
    stop: API.watchStop,
    quiet: API.watchQuiet,
    messages: API.watchMessages,
    say: API.watchSay,
    scrcpy: API.watchScrcpy,
    overlay: API.watchOverlay,
    adbPair: API.watchAdbPair,
    adbConnect: API.watchAdbConnect,
    adbQr: API.watchAdbQr,
    adbQrPoll: API.watchAdbQrPoll,
    frameUrl: API.watchFrameUrl,
    members: () => API.api('/api/chat?after=2147483647'),
  },
});
new Wander($('#wanderdlg'), $('#wanderbtn'), {
  api: {
    status: API.wanderStatus,
    start: API.wanderStart,
    stop: API.wanderStop,
    log: API.wanderLog,
    frameUrl: API.wanderFrameUrl,
  },
});
// 「工作台」页（分段栏第五个入口）：生成器 + 超级电脑 + dsh
const bench = new Bench($('#bench'));
const workbench = new Workbench(bench.pane('dsh'), bench.tabButton('dsh'), {
  api: { status: API.dshStatus, start: API.dshStart, stop: API.dshStop, check: API.dshCheck },
  isOpen: () => bench.visible('dsh'),
});
const superPanel = new SuperPanel(bench.pane('super'), document.querySelector('#entries [data-m="bench"]'), {
  api: {
    status: API.superStatus,
    submit: API.superSubmit,
    cancel: API.superCancel,
    task: API.superTask,
    limit: API.superLimit,
    wake: API.superWake,
  },
  isOpen: () => bench.visible('super'),
});
const benchGen = new BenchGenPanel(bench.pane('gen'), {
  api: {
    status: API.benchStatus,
    submit: API.benchSubmit,
    cancel: API.benchCancel,
    get: API.benchGet,
    ask: API.benchAsk,
    netCheck: API.benchNetCheck,
    desk: API.benchDesk,
    profileAdd: API.benchProfileAdd,
    profileRemove: API.benchProfileRemove,
    deskSuggest: API.benchDeskSuggest,
    answer: API.benchAnswer,
    sign: API.benchSign,
    review: API.benchReview,
    evolve: API.benchEvolve,
    plugin: API.benchPlugin,
    decide: API.benchDecide,
    material: API.benchMaterial,
    attach: API.benchAttach,
    look: API.benchLook,
  },
  isOpen: () => bench.visible('gen'),
  label: (id) => superPanel.label(id),
});
bench.onSelect = (name) =>
  name === 'dsh' ? workbench.open() : name === 'gen' ? benchGen.open() : superPanel.open();
$('#skin').addEventListener('click', () => {
  const next = document.documentElement.dataset.skin === 'ark' ? 'classic' : 'ark';
  document.documentElement.dataset.skin = next;
  try {
    localStorage.setItem('roundtable.skin', next);
  } catch {
    /* 存不了就只在这次生效 */
  }
});
const evolution = new Evolution($('#evolvedlg'), {
  api: {
    get: API.getEvolution,
    meet: API.evolutionMeet,
    exportList: API.evolutionExport,
    scout: API.evolutionScout,
    plugins: API.evolutionPlugins,
    decide: API.evolutionDecide,
    merge: API.evolutionMerge,
    setTheme: API.evolutionTheme,
  },
  openSession: async (sid) => {
    await setEntry('work');
    await open(sid);
  },
});
$('#evolve').addEventListener('click', () => evolution.open());
const usage = new Usage($('#usagedlg'), {
  api: { usage: API.getUsage, balance: API.getUsageBalance, priority: API.getChannelPriority, setPriority: API.setChannelPriority },
});
const moments = new Moments($('#moments'), {
  api: { list: API.getMoments, post: API.postMoment, like: API.likeMoment, comment: API.commentMoment, del: API.delMoment, delComment: API.delMomentComment },
  onSeen: () => gchat.markMomentsSeen(),
  avatar: (member, name) => (member ? gchat.avatar(member, name) : ''),
});
const gchat = new GroupChat($('#gchat'), {
  openPeople: (member) => people.open(member),
  openFeeds: () => feeds.open(),
  openMoments: (author) => moments.open(author),
  api: { get: API.getChat, post: API.postChat, pat: API.patChat, recall: API.recallChat, forward: API.forwardChat, clear: API.clearChat, rooms: API.getChatRooms, handoff: API.chatHandoff, lifeReports: API.lifeReports, look: API.chatLook },
  openSession: async (sid) => {
    await setEntry('work');
    await open(sid);
  },
});
// 成员专属表情（审过的）：装进表情面板，聊天和朋友圈里的记号画成图；在成员设定里审完后再取一次
async function loadMemberStickers() {
  try {
    setMemberPacks((await API.chatStickers()).packs);
  } catch {
    return; // 取不到就只用通用表情
  }
  if (gchat.built) gchat.renderAll();
  if ($('#moments').open) moments.render();
}
loadMemberStickers();
const home = new Apartment($('#home'), {
  api: API.getApartment,
  budget: API.getBudget,
  openSession: async (sid) => {
    await setEntry('work');
    await open(sid);
  },
  chatWith: async (name) => {
    await setEntry('chat');
    gchat.mentionInGroup(name);
  },
  dmWith: async (id) => {
    await setEntry('chat');
    gchat.openRoom(`dm:${id}`);
  },
  momentsOf: (id) => moments.open(id),
  personas: API.chatPersonas,
  setupOf: (id) => people.open(id),
});
const standee = new Standee({ load: API.standee, pointer: () => hub.follow });
const hub = new Hub($('#hub'), {
  music: new Music(),
  clock: new Clock(),
  notice: new Notice({
    load: API.getChangelog,
    calendar: { load: API.getCalendar, add: API.addCalendarDate, remove: API.removeCalendarDate },
  }),
  mail: new Mail({
    load: API.mailView,
    settings: API.mailSettings,
    login: API.mailLogin,
    refresh: API.mailRefresh,
    message: API.mailMessage,
    reminder: API.mailReminder,
    logout: API.mailLogout,
    clear: API.mailClear,
    mark: API.mailMark,
    category: API.mailCategory,
    images: API.mailImages,
    ai: API.mailAI,
  }),
  standee,
  sessions: API.listSessions,
  rooms: API.getChatRooms,
  status: () => S.status,
  go: (m, from) => setEntry(m, from),
  openPeople: () => people.open(),
  openUsage: () => usage.open(),
  openHistory: async () => {
    await setEntry('work');
    $('#tabs button[data-t="history"]').click();
  },
});
// 点气泡 / 菜单「跳到讨论」：滚到最新的讨论内容
function gotoDiscussion() {
  const feed = document.getElementById('feed');
  const last = document.querySelector('#feed-list')?.lastElementChild;
  (last || feed)?.scrollIntoView?.({ behavior: 'smooth', block: 'end' });
}
let petReady = false;
function syncPet() {
  const sv = S.session;
  // 公寓楼和群聊里有自己的小人：网页小人只在办公室出现
  if (petReady) pet.setAllowed(entry === 'work' && !(sv && sv.anonymous && !sv.revealed));
}

const prefix = () => S.status?.code_prefix || '组员';
const plans = () => S.status?.plans || {};

// --- 渲染 -----------------------------------------------------------------------

function render() {
  renderStage();
  renderFeed();
  renderPanel();
  renderHeader();
}

function renderStage() {
  const tables = S.session?.tables || [];
  const seats = Math.max(0, ...tables.map((t) => t.codes.length));
  $('#stage').classList.toggle('many', seats > 6); // 人多时座位缩小
  $('#stage').classList.toggle('dense', seats > 9);
  const stage = $('#stage');
  // 座位图整块重画，但花切动画留在原处，不然每个事件都会让循环从头开始
  for (const n of [...stage.children]) if (n !== deck.el) n.remove();
  stage.insertAdjacentHTML('afterbegin', stageHTML(S.session, S.live, prefix()));
  deck.mount(stage);
  const sv = S.session;
  deck.set(!!sv && sv.running && !RESTING.has(sv.status), () => S.session?.status);
  $('#st-state').textContent = stageState(sv);
}

/** 办公室标题旁的状态：空闲 / 第几桌 · 当前步骤 / 结束状态。 */
function stageState(sv) {
  if (!sv) return '空闲';
  if (S.live.step) {
    const no = S.live.table ?? 0;
    return `第 ${no + 1} 桌 · ${STEP_LABELS[S.live.step] || S.live.step}`;
  }
  return sv.running && !RESTING.has(sv.status) ? '进行中' : STATUS_LABELS[sv.status] || sv.status;
}

function renderLegend() {
  const sv = S.session;
  $('#legend').innerHTML = legendHTML(
    sv ? { anonymous: sv.anonymous, workflow: sv.workflow } : { anonymous: S.anonymous, workflow: S.workflow },
  );
}

function renderHeader() {
  renderLegend();
  syncPet();
  const sv = S.session;
  const b = S.budget;
  const part = (k, v, cap) => `<span>${k}<b>${esc(v)}</b>${cap ? `<small>/ ${esc(cap)}</small>` : ''}</span>`;
  const parts = [part('本场', money(sv ? sv.cost_usd : 0))];
  if (b?.month) parts.push(part('本月', money(b.month.spent_usd), money(b.month.limit_usd)));
  if (b?.day) parts.push(part('今日', money(b.day.spent_usd), money(b.day.limit_usd)));
  const meter = $('#meter');
  meter.innerHTML = parts.join('');
  const warn = !!(b && ((b.month && b.month.warn) || (b.day && b.day.warn)));
  meter.classList.toggle('warn', warn);
  meter.title = warn ? '预算已用超过提醒比例' : '';
  const rv = $('#reveal');
  rv.hidden = !!sv && !sv.anonymous; // 匿名关闭时身份一直公开，没有揭晓步骤
  rv.disabled = !(sv && sv.can_reveal);
  rv.textContent = sv && sv.anonymous && sv.revealed ? '已揭晓' : '揭晓身份';
  // 办公室模式：统筹就是经理
  $('.panel .phd h2').textContent = (sv ? sv.workflow : S.workflow) === 'office' ? '经理面板' : '统筹面板';
  $('#pstate').textContent = sv
    ? sv.running && sv.status !== 'awaiting_confirmation'
      ? '进行中'
      : STATUS_LABELS[sv.status] || sv.status
    : '空闲';
  const canNote = !!sv && sv.running && sv.status !== 'awaiting_confirmation';
  $('#noteform').hidden = !canNote;
  const q = $('#question');
  q.hidden = !sv;
  if (sv) q.innerHTML = `<span class="tag">题目</span>${esc(sv.question)}`;
}

const rendered = new Map(); // key -> {html, el}

function renderFeed() {
  const list = $('#feed-list');
  const feed = $('#feed');
  const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;
  if (!S.session) {
    rendered.clear();
    list.innerHTML = welcomeHTML();
  } else {
    const items = feedItems(S.session, {
      prefix: prefix(),
      plans: plans(),
      customLabel: S.status?.custom_label || '自选',
      threshold: S.status?.confirm_threshold_usd,
      live: S.live,
      cardError: S.cardError,
    });
    const els = [];
    const keep = new Set();
    for (const it of items) {
      let r = rendered.get(it.key);
      if (!r || r.html !== it.html) {
        const tpl = document.createElement('template');
        tpl.innerHTML = it.html.trim();
        r = { html: it.html, el: tpl.content.firstElementChild };
        rendered.set(it.key, r);
      } else {
        r.el.classList.add('seen');
      }
      keep.add(it.key);
      els.push(r.el);
    }
    for (const k of [...rendered.keys()]) if (!keep.has(k)) rendered.delete(k);
    // 只有内容或顺序变了才动 DOM：长讨论里每个事件都整体重排会卡顿、打断选中的文字
    const same = list.children.length === els.length && els.every((el, i) => list.children[i] === el);
    if (!same) list.replaceChildren(...els);
  }
  renderTyping();
  // 欢迎页从头看；有讨论时跟到最新（平板、手机上欢迎页常常比可视区高）
  if (!S.session) feed.scrollTop = 0;
  else if (atBottom) feed.scrollTop = feed.scrollHeight;
}

function renderTyping() {
  const t = $('#typing');
  const who = [...S.live.speaking].map((c) => (c === '统' ? '统筹' : `${prefix()}${c}`));
  if (!S.session || !S.live.step || !who.length) {
    t.hidden = true;
    return;
  }
  t.hidden = false;
  t.innerHTML = `<i></i><i></i><i></i><span>${esc(who.join('、'))} 正在${esc(STEP_LABELS[S.live.step] || S.live.step)}…</span>`;
}

function welcomeHTML() {
  const th = money(S.status?.confirm_threshold_usd ?? 0.3);
  return `<div class="welcome"><h2>新题目</h2>
<p class="lead">在下面写题目，选好办理方式后按 <kbd>Enter</kbd> 提交。提交前会列出预计花费，超过 ${th} 会先问你。</p>
<dl><dt>办公室</dt><dd>经理分诊，派一位员工办理，经理过目后交付。花费最少，默认用这个。</dd>
<dt>讨论</dt><dd>所选档位的成员都上桌：各自作答、互相评审、修订，最后由统筹汇总共识与分歧。</dd>
<dt>协同</dt><dd>把题目拆成子任务分给不同成员，做完再合并。适合篇幅长、分工明确的作业。</dd>
<dt>附件</dt><dd>图片、PDF、Word、文本、音频，拖进输入框即可。成员可以运行代码、写文件、画图和联网搜索，生成的文件在过程区和「工具」里预览、下载。</dd>
<dt>匿名</dt><dd>成员用塔罗牌代号（愚者、魔术师…），结束后再揭晓身份；不匿名时用「昵称·模式」，如「昵称·全力」。发给模型的内容不含模型 id 和厂商名。</dd></dl></div>`;
}

function renderPanel() {
  document
    .querySelectorAll('#tabs button')
    .forEach((b) => b.setAttribute('aria-selected', String(b.dataset.t === S.tab)));
  const ctx = {
    plans: plans(),
    threshold: S.status?.confirm_threshold_usd,
    officeThreshold: S.status?.office_threshold_usd ?? S.status?.confirm_threshold_usd,
    maxMembers: S.status?.max_members,
    workflow: S.workflow,
  };
  let h = '';
  if (S.tab === 'flow') h = flowPanel(S.session, S.live, ctx);
  if (S.tab === 'reviews') h = reviewsPanel(S.session, prefix());
  if (S.tab === 'split') h = splitPanel(S.session, prefix());
  if (S.tab === 'tools') h = toolsPanel(S.session, prefix());
  if (S.tab === 'media') h = mediaPanel(S.session, prefix());
  if (S.tab === 'usage') h = usagePanel(S.session, S.budget, S.status?.rice, S.payslip?.sid === S.sid ? S.payslip.data : null);
  if (S.tab === 'office') h = officePanel(S.office, S.status?.rice, S.talent, S.perfArmed);
  if (S.tab === 'channels') h = channelsPanel(S.status);
  if (S.tab === 'plugins') h = pluginBoardPanel(S.board, S.boardFilter);
  if (S.tab === 'models') h = directoryPanel(S.directory, S.dirFilter);
  if (S.tab === 'history') h = historyPanel(S.history, S.sid);
  if (S.tab === 'cast') h = castPanel(S.session, prefix());
  if (S.tab === 'contrib') h = contributionsPanel(S.session, S.contribHistory, prefix());
  $('#pbody').innerHTML = h;
}

// --- 提问区 ---------------------------------------------------------------------

function renderComposer() {
  const plans = S.status?.plans || {};
  if (!S.tier) S.tier = S.status?.default_plan || Object.keys(plans)[0] || 'custom';
  const options = { ...plans, custom: S.status?.custom_label || '自选' };
  $('#tier').innerHTML = Object.entries(options)
    .map(([k, label]) => `<button type="button" data-v="${esc(k)}" aria-pressed="${k === S.tier}">${esc(label)}</button>`)
    .join('');
  $('#anonymous').checked = S.anonymous;
  const workflows = S.status?.workflows || { office: '办公室', discussion: '讨论模式', collab: '协同模式', solo: '个人作答', brainstorm: '头脑风暴', auto: '自动' };
  $('#workflow').innerHTML = Object.entries(workflows)
    .map(([k, label]) => `<button type="button" data-v="${esc(k)}" aria-pressed="${k === S.workflow}" title="${{ office: '经理看题后派一位合适的员工办理，经理过目，不合格退回改一次；有争议或重要的事，经理点名两三位员工开会、依次发言后拍板；大件的活组项目组分工完成，另一位经理验收；只有需要时才查资料', brainstorm: '每人抽一种发散手法出点子，再互相杂交、匿名投票，统筹整合成点子榜、导图、对比表、场景故事和行动方案', auto: '开始时由几位最便宜的模型给出建议（用哪种方式、难度），你确认后再开始；简单题不调用它们', collab: '统筹拆分子任务，成员自荐、分工完成、交叉审查，最后合并', solo: '只请一位成员直接回答：在所选档位里挑最便宜的，不互评、不汇总' }[k] || '全员各自作答，互评、修订后由统筹汇总'}">${esc(label)}</button>`)
    .join('');
  renderMediaChoice();
  $('#manual').hidden = S.tier !== 'custom';
  const min = (S.status?.min_members ?? 2) + 1;
  // 每种方式只回答一类需求：适合什么、会得到什么
  const fit = {
    solo: '适合简单题，最快最省；得到一份答案。',
    discussion: '适合要“对”的题；得到最终答案、共识与分歧、纠错记录。',
    collab: '适合要完整成果的大活；得到合并成品和分工看板。',
    brainstorm: '适合找点子、想方向；得到点子榜、导图、对比表、场景故事和行动方案。',
    office: '适合日常交办；',
    auto: '不知道选哪种时用：先给建议，你确认后再开始。',
  }[S.workflow] || '';
  $('#mode-hint').textContent = fit + (
    S.workflow === 'office'
      ? `经理分诊后派一位员工办理并过目，必要时开会或组项目组；预计超过 ${money(S.status?.office_threshold_usd ?? 0.05)} 先问你`
      : S.tier === 'custom'
      ? `勾选至少 ${min} 个模型，全部上桌（其中一个当统筹）`
      : `该档位所有可用模型上桌；预计超过 ${money(S.status?.confirm_threshold_usd ?? 0.3)} 先问你`);
  renderCarry();
}

function renderMediaChoice() {
  const info = S.status?.media;
  const kinds = info?.kinds || {};
  const collab = S.workflow === 'collab';
  if (collab || ['office', 'solo', 'auto', 'brainstorm'].includes(S.workflow)) S.media = null; // 协同模式由统筹决定哪些子任务生成媒体；个人作答只出文字
  const outputs = [[null, '文字'], ...Object.entries(kinds).map(([k, v]) => [k, v.label])];
  if (['office', 'solo', 'auto', 'brainstorm'].includes(S.workflow)) {
    const why = { office: '办公室模式先只出文字（员工可以用画图工具）', auto: '自动模式先出建议，输出类型请在选定作答方式后再选', brainstorm: '头脑风暴输出点子榜、导图和行动方案' }[S.workflow] || '个人作答只输出文字';
    $('#media').innerHTML = `<span class="hint" id="media-auto">${why}</span>`;
  } else if (collab) {
    // 协同模式不选输出类型：不显示一排灰按钮（容易误以为不能画图），直接说明
    $('#media').innerHTML = '<span class="hint" id="media-auto">输出类型由统筹按任务自动决定</span>';
  } else
  $('#media').innerHTML = outputs
    .map(([k, label]) => {
      const off = k !== null && (collab || !kinds[k].available);
      const why = collab ? '协同模式下由统筹决定哪些子任务要生成媒体' : (kinds[k]?.reason || '');
      return `<button type="button" data-v="${esc(k ?? '')}" aria-pressed="${k === S.media}"${off ? ' disabled' : ''}${off && why ? ` title="${esc(why)}"` : ''}>${esc(label)}</button>`;
    })
    .join('');
  $('#media').hidden = !info || !Object.keys(kinds).length;
  // 质量档位：选了媒体输出，或协同模式（媒体子任务也用它）时显示
  const showTier = !!info && (S.media !== null || collab) && Object.values(kinds).some((v) => v.available);
  $('#mediatier').hidden = !showTier;
  $('#mediatier').innerHTML = [['budget', '普通'], ['flagship', '高质量']]
    .map(([k, label]) => `<button type="button" data-v="${k}" aria-pressed="${k === S.mediaTier}" title="媒体模型的档位：${k === 'budget' ? '便宜' : '更贵、质量更高'}">${label}</button>`)
    .join('');
}

// --- 接力 ------------------------------------------------------------------------

const CARRY_ASK = {
  discussion: '在上一场成果的基础上继续讨论：找出不足、补充完善，给出更好的结论。',
  collab: '按上一场的结论分工动手，做出完整成果。',
  brainstorm: '围绕上一场的结论继续发散，找更多点子和方向。',
  solo: '在上一场成果的基础上单独完成，给出最终版。',
  office: '按上一场的结论把这件事办好。',
};
const ASK_PLACEHOLDER = $('#ask')?.getAttribute('placeholder') || '';

async function startCarry(workflow, make = null) {
  const sid = S.sid;
  if (!sid) return;
  try {
    const info = await API.carry(sid);
    S.carry = { sid, question: info.question, summary: info.summary, workflow, model: '', ask: make?.ask || '', make: make?.label || '' };
    S.anonymous = info.anonymous;
  } catch (e) {
    formError(e.message);
    return;
  }
  S.workflow = workflow;
  if (make) S.media = make.kind;
  renderComposer();
  renderLegend();
  $('#ask').focus();
  scheduleEstimate();
}

/** 转成作品：讨论模式 + 选好的输出类型，带上这一场的成果摘要；用户看过预估再提交。 */
function startMake(key) {
  const target = MAKE_TARGETS.find(([k]) => k === key);
  if (!target) return;
  const [, kind, label, ask] = target;
  const info = S.status?.media?.kinds?.[kind];
  if (!info?.available) {
    formError(info?.reason || `现在没有可用的${label}生成模型`);
    return;
  }
  startCarry('discussion', { kind, label, ask });
}

function renderCarry() {
  const box = $('#carry');
  const c = S.carry;
  box.hidden = !c;
  $('#ask').setAttribute('placeholder', c ? `这一场要做什么（可留空：${c.ask || CARRY_ASK[S.workflow] || CARRY_ASK.discussion}）` : ASK_PLACEHOLDER);
  if (!c) {
    box.innerHTML = '';
    return;
  }
  const short = (t, n) => (t.length > n ? `${t.slice(0, n)}…` : t);
  let who = '';
  if (S.workflow === 'solo') {
    const opts = (S.status?.models || []).filter((m) => m.available)
      .map((m) => `<option value="${esc(m.id)}"${m.id === c.model ? ' selected' : ''}>${esc(m.id)}</option>`).join('');
    who = `<label class="hint">交给 <select id="carry-who" aria-label="交给谁"><option value="">所选档位里最便宜的一位</option>${opts}</select></label>`;
  }
  box.innerHTML = `<div class="carry-head"><span class="tag">${c.make ? `转成作品 · ${esc(c.make)}` : '接着上一场'}</span><b>${esc(short(c.question, 60))}</b>${who}<button class="btn sm" type="button" data-act="carry-off" title="不带上一场的成果">取消接力</button></div>
<details><summary class="hint">将带入的成果摘要（约 ${c.summary.length} 字，不带过程）</summary><div class="carry-sum">${esc(c.summary)}</div></details>`;
}

function carryFields(body) {
  if (!S.carry) return body;
  body.carry_from = S.carry.sid;
  if (!body.question) body.question = S.carry.ask || CARRY_ASK[S.workflow] || CARRY_ASK.discussion;
  if (S.workflow === 'solo' && S.carry.model) {
    body.tier = 'custom';
    body.models = [S.carry.model];
    delete body.coordinator;
  }
  return body;
}

function soloPick() {
  return !!(S.carry && S.workflow === 'solo' && S.carry.model);
}

function renderPicks() {
  const models = S.status?.models || [];
  const tierLabel = { flagship: '全力', budget: '节电' };
  $('#picks').innerHTML = models
    .map(
      (m) =>
        `<label class="pick${m.available ? '' : ' off'}"><input type="checkbox" value="${esc(m.id)}"${m.available ? '' : ' disabled'}>${esc(m.id)}<span class="tier">${esc(tierLabel[m.tier] || m.tier)} · $${m.price.input}/$${m.price.output}</span></label>`,
    )
    .join('');
  $('#coordinator').innerHTML =
    '<option value="">自动选择</option>' +
    models
      .filter((m) => m.available)
      .map((m) => `<option value="${esc(m.id)}">${esc(m.id)}</option>`)
      .join('');
}

// --- 附件与提交前预估 ---------------------------------------------------------------

function renderAttachments() {
  const el = $('#attachments');
  el.hidden = !S.attachments.length;
  el.innerHTML = attachmentsHTML(S.attachments);
}

async function addFiles(files) {
  const max = S.status?.uploads?.max_files ?? 5;
  for (const file of files) {
    if (S.attachments.filter((a) => !a.error).length >= max) {
      formError(`最多添加 ${max} 个附件`);
      break;
    }
    const item = { name: file.name, pending: true };
    S.attachments.push(item);
    renderAttachments();
    try {
      Object.assign(item, await API.upload(file), { pending: false });
    } catch (e) {
      Object.assign(item, { pending: false, error: e.message });
    }
    renderAttachments();
  }
  scheduleEstimate();
}

// 资料浏览器里点「附上」：资料已经变成一个附件，直接放进提问区
function addClip(att) {
  const max = S.status?.uploads?.max_files ?? 5;
  if (S.attachments.filter((a) => !a.error).length >= max) throw new Error(`最多添加 ${max} 个附件`);
  S.attachments.push({ ...att, pending: false });
  renderAttachments();
  scheduleEstimate();
}

function estimateRequest() {
  const question = $('#ask').value.trim();
  if (!question && !S.carry) return null;
  const req = {
    question,
    tier: S.tier,
    anonymous: S.anonymous,
    workflow: S.workflow,
    attachments: S.attachments.filter((a) => a.id).map((a) => a.id),
  };
  if (S.workflow === 'auto') delete req.workflow; // 预估区列出全部方式，供对比
  if (S.media) req.media = S.media;
  if (S.media) req.media_tier = S.mediaTier;
  if (S.tier === 'custom' && !soloPick()) {
    req.models = [...document.querySelectorAll('#picks input:checked')].map((i) => i.value);
    if (req.models.length < (S.status?.min_members ?? 2) + 1) return null;
    const coord = $('#coordinator').value;
    if (coord && req.models.includes(coord)) req.coordinator = coord;
  }
  return carryFields(req);
}

let estTimer = null;
function scheduleEstimate() {
  clearTimeout(estTimer);
  estTimer = setTimeout(runEstimate, 350);
}

async function runEstimate() {
  const req = estimateRequest();
  const el = $('#estimate');
  if (!req) {
    S.estimate = null;
    S.estKey = '';
    el.hidden = true;
    return;
  }
  const key = JSON.stringify(req);
  try {
    const est = await API.estimate({ ...req, seed: S.estSeed ?? undefined });
    if (JSON.stringify(estimateRequest()) !== key) return; // 输入又变了
    S.estimate = est;
    S.estKey = key;
    S.estSeed = est.seed;
    renderEstimate();
  } catch (e) {
    S.estimate = null;
    S.estKey = '';
    el.hidden = false;
    el.textContent = `无法预估：${e.message}`;
  }
}

function renderEstimate() {
  const el = $('#estimate');
  const html = estimateHTML(S.estimate, {
    workflow: S.workflow === 'auto' ? 'discussion' : S.workflow,
    anonymous: S.anonymous,
    plans: plans(),
    customLabel: S.status?.custom_label || '自选',
  });
  el.hidden = !html;
  el.innerHTML = html;
}

async function showPreview(fid) {
  const dlg = $('#viewer');
  dlg.innerHTML = '<div class="vb">加载中…</div>';
  if (!dlg.open) dlg.showModal();
  try {
    dlg.innerHTML = previewHTML(S.sid, await API.filePreview(S.sid, fid));
  } catch (e) {
    dlg.innerHTML = `<div class="vh"><b>预览失败</b><button class="btn sm" type="button" data-close>关闭</button></div><div class="vb">${esc(e.message)}</div>`;
  }
}

function formError(text) {
  const e = $('#formerr');
  e.hidden = !text;
  e.textContent = text || '';
}

async function submit(ev) {
  ev.preventDefault();
  if ($('#submit').disabled) return; // 正在提交：连按 Enter 不会重复提交
  const question = $('#ask').value.trim();
  if (!question && !S.carry) {
    formError('请先输入题目');
    return;
  }
  if (S.attachments.some((a) => a.pending)) {
    formError('附件还在上传，请稍等');
    return;
  }
  const body = {
    question,
    tier: S.tier,
    anonymous: S.anonymous,
    workflow: S.workflow,
    attachments: S.attachments.filter((a) => a.id).map((a) => a.id),
  };
  if (S.media) body.media = S.media;
  if (S.media || S.workflow === 'collab') body.media_tier = S.mediaTier;
  // 取消勾选"风格参考"的图片只是普通附件：只有存在取消时才传 style_refs（省略 = 全部图片）
  const images = S.attachments.filter((a) => a.id && a.kind === 'image');
  if (images.some((a) => a.style_ref === false)) body.style_refs = images.filter((a) => a.style_ref !== false).map((a) => a.id);
  if (S.tier === 'custom' && !soloPick()) {
    body.models = [...document.querySelectorAll('#picks input:checked')].map((i) => i.value);
    const min = (S.status?.min_members ?? 2) + 1;
    if (body.models.length < min) {
      formError(`自选至少勾选 ${min} 个模型`);
      return;
    }
    const coord = $('#coordinator').value;
    if (coord) {
      if (!body.models.includes(coord)) {
        formError('统筹必须是勾选的模型之一');
        return;
      }
      body.coordinator = coord;
    }
  }
  carryFields(body);
  formError('');
  if (S.estimate && S.estKey === JSON.stringify(estimateRequest())) body.seed = S.estimate.seed;
  $('#submit').disabled = true;
  try {
    const { session_id } = await API.createSession(body);
    $('#ask').value = '';
    S.carry = null;
    renderCarry();
    S.attachments = [];
    S.estimate = null;
    S.estSeed = null;
    renderAttachments();
    renderEstimate();
    await open(session_id);
  } catch (e) {
    formError(e.message);
  } finally {
    $('#submit').disabled = false;
  }
}

// --- 会话与事件流 -----------------------------------------------------------------

async function open(sid) {
  stopStream();
  S.sid = sid;
  S.live = freshLive();
  pet.resetReport();
  S.cardError = null;
  rendered.clear();
  try {
    history.replaceState(null, '', `#s=${sid}`);
  } catch {
    /* 忽略 */
  }
  await refresh();
  connect();
}

function stopStream() {
  if (S.stream) S.stream.abort();
  S.stream = null;
}

function connect() {
  const sid = S.sid;
  if (!sid) return;
  stopStream();
  const ctrl = new AbortController();
  S.stream = ctrl;
  API.streamEvents(sid, (e) => onEvent(sid, e), ctrl.signal)
    .catch((err) => {
      if (err.name !== 'AbortError') console.warn('事件流中断', err);
    })
    .finally(() => {
      if (S.stream === ctrl) S.stream = null;
      if (S.sid === sid) refresh(true);
    });
}

let timer = null;
function scheduleRefresh() {
  if (timer) return;
  timer = setTimeout(() => {
    timer = null;
    refresh();
  }, 120);
}

async function refresh(all = false) {
  const sid = S.sid;
  if (!sid) {
    render();
    return;
  }
  try {
    const sv = await API.getSession(sid);
    if (S.sid !== sid) return;
    S.session = sv;
    if (!sv.running) {
      S.live.step = null;
      S.live.speaking.clear();
      S.live.act = '';
    }
  } catch (e) {
    if (e.status === 404) {
      S.sid = null;
      S.session = null;
    }
  }
  if (all) await Promise.all([loadBudget(), loadHistory(), loadContributions()]);
  render();
}

/** 办公室模式第 1 桌的安排（单人办 / 开会 / 项目分工）：实时事件里有就用，否则从已保存的分诊结果读。 */
function officePlan(tableNo) {
  if (tableNo !== 0 || S.session?.workflow !== 'office') return null;
  let plan = null;
  for (const o of S.session.outputs || []) {
    if (o.table_no !== 0 || (o.kind !== 'office_intake' && o.kind !== 'office_approval')) continue;
    let d = null;
    try {
      d = JSON.parse(o.content);
    } catch {
      continue;
    }
    plan = { ...(plan || {}), ...d };
  }
  return plan || S.live.office || null;
}

function onEvent(sid, e) {
  if (sid !== S.sid) return;
  pet.report(e);
  const L = S.live;
  switch (e.type) {
    case 'snapshot':
      break;
    case 'routed':
      L.act = `${plans()[e.data.plan] || e.data.plan} · 预计 ${money(e.data.estimate_usd)}`;
      break;
    case 'table_started':
      L.table = e.table_no;
      L.act = '';
      break;
    case 'step_started': {
      L.table = e.table_no;
      L.step = e.step;
      const t = S.session?.tables?.find((x) => x.table_no === e.table_no);
      const dropped = new Set(
        (S.session?.outputs || []).filter((o) => o.kind === 'dropout' && o.table_no === e.table_no).map((o) => o.code),
      );
      L.speaking = new Set();
      const plan = e.step === 'handle' ? officePlan(e.table_no) : null;
      if (COORD_STEPS.has(e.step)) L.speaking.add('统');
      else if (plan) {
        // 办公室模式：只有派到的员工（开会时是参会的员工）在干活
        const busy = plan.mode === 'meeting' ? plan.attendees || [] : plan.mode === 'project' ? [] : [plan.assignee];
        busy.filter((c) => c && !dropped.has(c)).forEach((c) => L.speaking.add(c));
      } else if (e.step !== 'reveal') (t?.codes || []).filter((c) => !dropped.has(c)).forEach((c) => L.speaking.add(c));
      L.act = plan && plan.mode === 'meeting' ? '开会中' : `${STEP_LABELS[e.step] || e.step}中`;
      break;
    }
    case 'office_assigned':
      L.office = { mode: e.data.mode, attendees: e.data.attendees || [], assignee: e.code };
      L.act = e.data.mode === 'meeting' ? `经理决定开会（${(e.data.attendees || []).length} 位参会）` : e.data.mode === 'project' ? `经理决定项目分工（${(e.data.attendees || []).length} 人）` : '经理已派活';
      break;
    case 'office_project':
      L.act = `另开项目组桌子：${e.data.members} 人，预计 ${money(e.data.estimate_usd)}`;
      break;
    case 'call_done':
    case 'call_failed':
      L.speaking.delete(e.code || '统');
      break;
    case 'step_finished':
      L.step = null;
      L.speaking = new Set();
      loadBudget();
      break;
    case 'escalating':
      L.act = `升级中：${e.data.reason || ''}`;
      break;
    case 'media_started':
      L.act = `生成${MEDIA_LABELS[e.data.kind] || '媒体'}（第 ${e.data.round} 轮）…`;
      break;
    case 'media_progress':
      L.act = `视频${e.data.state === 'pending' ? '排队中' : '生成中'}（第 ${e.data.round} 轮）…`;
      break;
    case 'media_done':
      L.act = `${MEDIA_LABELS[e.data.kind] || '媒体'}已生成，评审中…`;
      break;
    case 'note_added':
    case 'notes_applied':
      refresh();
      break;
    case 'budget_warning':
      L.warnings.push(e.data.message);
      break;
    case 'checkpoint':
      L.step = null;
      L.speaking = new Set();
      L.act = '等待你决定';
      break;
    case 'state':
      if (RESTING.has(e.status)) {
        L.step = null;
        L.speaking = new Set();
      }
      break;
    default:
      break;
  }
  renderStage();
  renderTyping();
  scheduleRefresh();
}

async function answer(cpId, key) {
  if (S.busy || !S.sid) return;
  S.busy = true;
  S.cardError = null;
  document.querySelectorAll(`[data-cp="${cpId}"]`).forEach((b) => (b.disabled = true));
  try {
    await API.respond(S.sid, key);
    S.live.act = '';
    connect();
  } catch (e) {
    S.cardError = e.message;
    await refresh();
  } finally {
    S.busy = false;
  }
}

async function doResume() {
  if (!S.sid) return;
  try {
    await API.resume(S.sid);
    connect();
  } catch (e) {
    formError(e.message);
  }
}

async function doReveal() {
  if (!S.sid) return;
  $('#reveal').disabled = true;
  try {
    S.session = await API.reveal(S.sid);
    rendered.clear();
    await loadHistory();
    render();
  } catch (e) {
    formError(e.message);
    renderHeader();
  }
}

async function loadBudget() {
  try {
    S.budget = await API.getBudget();
  } catch {
    /* 下次再试 */
  }
  renderHeader();
  if (S.tab === 'usage') {
    renderPanel();
    loadPayslip();
  }
}

async function loadPayslip() {
  if (!S.sid) return;
  const sid = S.sid;
  try {
    S.payslip = { sid, data: await API.getPayslip(sid) };
  } catch {
    /* 下次再试 */
  }
  if (S.tab === 'usage') renderPanel();
}

async function loadOffice() {
  try {
    S.office = await API.getOffice();
  } catch {
    /* 下次再试 */
  }
  if (S.tab === 'office') renderPanel();
  if (S.talent) return;
  try {
    S.talent = await API.getCandidates();
  } catch {
    return;
  }
  if (S.tab === 'office') renderPanel();
  if (S.talent.available && S.talent.stale) refreshTalent(); // 超过一天没查：自动查一次（只提示，不改配置）
}

/** 模型图鉴：第一次打开时读缓存；超过一天没查就自动重新拉取（只浏览，不改配置）。 */
async function loadDirectory() {
  if (S.directory && !S.directory.error) return;
  try {
    S.directory = await API.getDirectory();
  } catch (e) {
    S.directory = { error: e.message || '加载失败', available: true };
  }
  if (S.tab === 'models') renderPanel();
  if (S.directory.available && S.directory.stale) refreshDirectory();
}

async function refreshDirectory() {
  if (!S.directory || S.directory.loading) return;
  S.directory = { ...S.directory, loading: true, error: null };
  if (S.tab === 'models') renderPanel();
  try {
    S.directory = await API.refreshDirectory();
  } catch (e) {
    S.directory = { ...S.directory, loading: false, error: e.message || '查询失败' };
  }
  if (S.tab === 'models') renderPanel();
}

function renderDirectoryList() {
  const list = $('#mdir-list');
  if (list && S.directory) list.innerHTML = directoryList(S.directory, S.dirFilter);
}

/** 插件榜：每次切到页签都重新拉取（数据文件每周更新，不用重启）。 */
async function loadBoard() {
  try {
    S.board = await API.getPluginBoard();
  } catch (e) {
    S.board = { error: e.message || '加载失败' };
  }
  if (S.tab === 'plugins') renderPanel();
}

/** 绩效清零：两步确认（第一次点只是武装，5 秒内再点一次才清零）。 */
async function resetPerformance() {
  if (!S.perfArmed) {
    S.perfArmed = true;
    renderPanel();
    setTimeout(() => {
      if (S.perfArmed) {
        S.perfArmed = false;
        if (S.tab === 'office') renderPanel();
      }
    }, 5000);
    return;
  }
  S.perfArmed = false;
  try {
    S.office = await API.resetPerformance();
  } catch (e) {
    formError(e.message || '清零失败');
  }
  if (S.tab === 'office') renderPanel();
}

/** 人才市场：重新查询聚合渠道的模型列表。 */
async function refreshTalent() {
  if (!S.talent || S.talent.loading) return;
  S.talent = { ...S.talent, loading: true, error: null };
  if (S.tab === 'office') renderPanel();
  try {
    S.talent = await API.refreshCandidates();
  } catch (e) {
    S.talent = { ...S.talent, loading: false, error: e.message || '查询失败' };
  }
  if (S.tab === 'office') renderPanel();
}

async function loadContributions() {
  try {
    S.contribHistory = await API.getContributions();
  } catch {
    /* 下次再试 */
  }
  if (S.tab === 'contrib') renderPanel();
}

async function loadHistory() {
  try {
    S.history = await API.listSessions();
  } catch {
    /* 下次再试 */
  }
  if (S.tab === 'history') renderPanel();
}

function newQuestion() {
  stopStream();
  S.sid = null;
  S.session = null;
  S.live = freshLive();
  pet.resetReport();
  try {
    history.replaceState(null, '', location.pathname);
  } catch {
    /* 忽略 */
  }
  render();
  $('#ask').focus();
}

// --- 键盘 -----------------------------------------------------------------------

// 输入法组合输入（中文选字）中按下的键：keyCode 229 兼容旧浏览器
function composing(e) {
  return e.isComposing || e.keyCode === 229;
}

function typing(el) {
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}

// --- 入口：办公室 / 公寓楼 / 群聊 / 终端 / 工作台 ------------------------------------------------------------

const ENTRIES = ['work', 'home', 'chat', 'hub', 'bench'];
let entry = 'work';

/** 打开网页时进哪个入口：默认终端首页；终端里可以勾选直接进办公室（存在本机）。本机存储不能用时进办公室。 */
function landing() {
  try {
    return localStorage.getItem('roundtable.landing') === 'work' ? 'work' : 'hub';
  } catch {
    return 'work';
  }
}

/** 切换入口。终端是首页；办公室是原来的界面；公寓楼是只读的基建式剖面图；群聊和成员聊天。地址栏 #m=hub / #m=home / #m=chat 刷新后回到原入口。 */
const VIEW_OF = { work: '#main', home: '#home', chat: '#gchat', hub: '#hub', bench: '#bench' };

/**
 * 切换入口，带过渡（仿 iPhone 打开 App，不回弹）：背景和顶栏不动，只有页面在动。
 * 旧页往前推远淡出，新页从略小处浮上来；从首页卡片点进去时，卡片本身放大成新页面。
 * 为了起止都顺：新页先把数据取回、排好版（最多等 MOTION_HOLD_MS）再开始动，动的过程中不再重排；
 * 卡片放大只用 transform（不动宽高），全程只改 transform 和 opacity。
 * 不支持视图过渡或系统开了「减少动态」时直接切换。from 是被点的卡片（可选）。
 * 返回的 Promise 在页面切过去（新页已显示、旧页已藏起）后完成，接着要操作新页面的调用方先 await 它。
 */
const MOTION_MS = 560;
const MOTION_HOLD_MS = 350;
const MOTION_EASE = 'cubic-bezier(.32,.72,0,1)';
function setEntry(m, from = null) {
  if (!ENTRIES.includes(m)) m = 'work';
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!document.startViewTransition || still || m === entry || !document.documentElement.dataset.entry) {
    applyEntry(m);
    return Promise.resolve();
  }
  const leave = $(VIEW_OF[entry]);
  const dest = $(VIEW_OF[m]);
  const name = (el, n) => {
    if (el) el.style.viewTransitionName = n;
  };
  const start = from?.getBoundingClientRect();
  let switched;
  const done = new Promise((r) => (switched = r));
  name(leave, 'rt-from');
  name(from, 'rt-zoom');
  const t = document.startViewTransition(async () => {
    name(leave, '');
    name(from, '');
    const shown = applyEntry(m);
    name(dest, from ? 'rt-zoom' : 'rt-to');
    switched();
    // 等数据画好、新页要用的字下载完（排一次版才会开始下载），免得动到一半换字体重排
    const settled = (async () => {
      await shown;
      dest?.getBoundingClientRect();
      await document.fonts.ready;
    })();
    await Promise.race([settled, new Promise((r) => setTimeout(r, MOTION_HOLD_MS))]).catch(() => {});
  });
  // 连点时上一个过渡会被跳过（ready 被拒绝），这里接住，不当成错误
  t.ready.then(() => from && dest && zoomFrom(start, dest.getBoundingClientRect())).catch(() => {});
  t.finished
    .finally(() => {
      name(leave, '');
      name(from, '');
      name(dest, '');
    })
    .catch(() => {});
  t.updateCallbackDone.catch(() => {}).finally(switched);
  return done;
}

/** 空闲时先把各页面用到的字下载好，第一次切过去时等得更短。 */
function warmFonts() {
  const text = Object.values(VIEW_OF).map((sel) => $(sel)?.textContent || '').join('');
  const css = getComputedStyle(document.documentElement);
  for (const v of ['--f-body', '--f-display'])
    for (const w of [400, 500]) document.fonts.load(`${w} 16px ${css.getPropertyValue(v)}`, text).catch(() => {});
}

/**
 * 停在终端首页时，空闲里把办公室和公寓楼几乎透明地画一遍再藏起来：第一次画大页面时显卡要多花约 0.1 秒，
 * 预先画过，第一次点进去的放大动画就不会在中途卡一下。
 */
async function warmViews() {
  const frames = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50))));
  const ghost = 'position:fixed;inset:64px 0 0;opacity:.002;pointer-events:none;z-index:-1';
  for (const [key, el, show, hide] of [
    ['work', $('#main'), () => ($('#main').hidden = false), () => ($('#main').hidden = entry !== 'work')],
    ['home', $('#home'), () => home.show(), () => entry !== 'home' && home.hide()],
  ]) {
    if (entry !== 'hub' || !el) return;
    el.style.cssText += `;${ghost}`;
    try {
      await show();
      await frames();
    } catch {
      /* 预热失败不影响使用 */
    }
    el.style.position = el.style.inset = el.style.opacity = el.style.pointerEvents = el.style.zIndex = '';
    if (entry !== key) hide();
  }
}

/** 卡片 → 整页：从卡片的位置和大小（a）放大到新页（b），只动 transform。 */
function zoomFrom(a, b) {
  if (!a || !b.width || !b.height) return;
  const at = (r, sx = 1, sy = 1) => `translate(${r.left}px, ${r.top}px) scale(${sx}, ${sy})`;
  document.documentElement.animate(
    { transform: [at(a, a.width / b.width, a.height / b.height), at(b)] },
    { duration: MOTION_MS, easing: MOTION_EASE, fill: 'both', pseudoElement: '::view-transition-group(rt-zoom)' },
  );
}

/** 切到入口 m；返回新页面数据取回、画好的 Promise（过渡等它再开始动）。 */
function applyEntry(m) {
  entry = m;
  for (const b of document.querySelectorAll('#entries button')) b.setAttribute('aria-pressed', String(b.dataset.m === m));
  $('#main').hidden = m !== 'work';
  $('#workctrls').hidden = m !== 'work';
  $('#meter').hidden = m !== 'work';
  let shown = Promise.resolve();
  if (m === 'home') shown = home.show();
  else home.hide();
  // 群聊画过一次后，再进来先用现有内容开始动，新消息在后台拉（增量，很小）
  if (m === 'chat') shown = gchat.built ? (gchat.show(), Promise.resolve()) : gchat.show();
  else gchat.hide();
  if (m === 'hub') shown = hub.show();
  else hub.hide();
  if (m === 'bench') shown = bench.show();
  else bench.hide();
  document.documentElement.dataset.entry = m;
  syncPet();
  if (m !== 'work') history.replaceState(null, '', `#m=${m}`);
  else if (location.hash.startsWith('#m=')) history.replaceState(null, '', S.sid ? `#s=${S.sid}` : location.pathname);
  return shown;
}

/**
 * 全局快捷键（不在输入框里时）：
 * / 或 Ctrl+K 聚焦题目输入框；Esc 离开输入框；1～9 选择等待确认卡片的对应选项；
 * N 新题目；Alt+1 / 2 / 3 / 4 / 5 切换办公室 / 公寓楼 / 群聊 / 终端 / 工作台。输入框里只处理 Esc、Ctrl+K 与 Alt+数字。
 */
function shortcut(e) {
  if (composing(e) || e.defaultPrevented) return;
  const el = document.activeElement;
  // Alt+1 / 2 / 3 / 4：切换入口（按物理键位判断，Mac 上 Option+数字会打出特殊字符）
  const digit = /^Digit([1-9])$/.exec(e.code || '');
  if (e.altKey && !e.ctrlKey && !e.metaKey && digit && ENTRIES[Number(digit[1]) - 1]) {
    e.preventDefault();
    setEntry(ENTRIES[Number(digit[1]) - 1]);
    return;
  }
  if (entry !== 'work') return; // 其余快捷键只在办公室里用
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    $('#ask').focus();
    return;
  }
  if (e.key === 'Escape') {
    if ($('#viewer').open) return; // 预览框自己处理 Esc
    if (typing(el)) el.blur();
    return;
  }
  if (typing(el) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '/') {
    e.preventDefault();
    $('#ask').focus();
    return;
  }
  if (/^[1-9]$/.test(e.key)) {
    const opts = [...document.querySelectorAll('#feed .opt[data-opt]:not([disabled])')];
    const b = opts[Number(e.key) - 1];
    if (b) {
      e.preventDefault();
      b.click();
    }
    return;
  }
  if (e.key === 'n' || e.key === 'N') {
    e.preventDefault();
    $('#new').click();
  }
}

// --- 启动 -----------------------------------------------------------------------

function bind() {
  $('#composer').addEventListener('submit', submit);
  $('#entries').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-m]');
    if (b) setEntry(b.dataset.m);
  });
  $('#ask').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || composing(e)) return; // 输入法选字时的 Enter 只确认候选词
    if (e.shiftKey || e.altKey) return; // Shift+Enter / Alt+Enter 换行
    e.preventDefault();
    $('#composer').requestSubmit();
  });
  document.addEventListener('keydown', shortcut);
  $('#tabs').addEventListener('keydown', (e) => {
    // 面板标签页：← → 切换（Home / End 到头尾）
    const tabs = [...$('#tabs').querySelectorAll('button')];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    const b = tabs[(next + tabs.length) % tabs.length];
    b.focus();
    b.click();
  });
  $('#tier').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    S.tier = b.dataset.v;
    formError('');
    renderComposer();
    scheduleEstimate();
  });
  $('#ask').addEventListener('input', scheduleEstimate);
  $('#picks').addEventListener('change', scheduleEstimate);
  $('#carry').addEventListener('change', (e) => {
    if (e.target.id !== 'carry-who' || !S.carry) return;
    S.carry.model = e.target.value;
    scheduleEstimate();
  });
  $('#carry').addEventListener('click', (e) => {
    if (!e.target.closest('[data-act="carry-off"]')) return;
    S.carry = null;
    renderCarry();
    scheduleEstimate();
  });
  $('#coordinator').addEventListener('change', scheduleEstimate);
  $('#attach').addEventListener('click', () => $('#file').click());
  $('#clipbtn').addEventListener('click', () => clipper.open());
  $('#file').addEventListener('change', (e) => {
    const files = [...e.target.files];
    e.target.value = '';
    addFiles(files);
  });
  $('#attachments').addEventListener('change', (e) => {
    const box = e.target.closest('[data-sref]');
    if (!box) return;
    S.attachments[Number(box.dataset.sref)].style_ref = box.checked;
  });
  $('#attachments').addEventListener('click', (e) => {
    const b = e.target.closest('[data-rm]');
    if (!b) return;
    S.attachments.splice(Number(b.dataset.rm), 1);
    renderAttachments();
    scheduleEstimate();
  });
  $('#noteform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = $('#note');
    const text = input.value.trim();
    if (!text || !S.sid) return;
    $('#noteerr').textContent = '';
    try {
      await API.addNote(S.sid, text);
      input.value = '';
      await refresh();
    } catch (err) {
      $('#noteerr').textContent = err.message;
    }
  });
  const comp = $('#composer');
  comp.addEventListener('dragover', (e) => {
    if (![...(e.dataTransfer?.types || [])].includes('Files')) return;
    e.preventDefault();
    comp.classList.add('drag');
  });
  comp.addEventListener('dragleave', () => comp.classList.remove('drag'));
  comp.addEventListener('drop', (e) => {
    e.preventDefault();
    comp.classList.remove('drag');
    addFiles([...(e.dataTransfer?.files || [])]);
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('#talent-refresh')) refreshTalent();
    if (e.target.closest('#mdir-refresh')) refreshDirectory();
    const mc = e.target.closest('#mdir-cat [data-c]');
    if (mc) {
      S.dirFilter = { ...S.dirFilter, cat: mc.dataset.c, tag: '', limit: 80 };
      renderPanel();
    }
    const mt = e.target.closest('#mdir-tag [data-g]');
    if (mt) {
      S.dirFilter = { ...S.dirFilter, tag: S.dirFilter.tag === mt.dataset.g ? '' : mt.dataset.g, limit: 80 };
      renderPanel();
    }
    if (e.target.closest('#mdir-more')) {
      S.dirFilter = { ...S.dirFilter, limit: S.dirFilter.limit + 80 };
      renderDirectoryList();
    }
    const cp = e.target.closest('[data-copy-route]');
    if (cp && S.directory) {
      const entry = (S.directory.entries || []).find((x) => x.route === cp.dataset.copyRoute);
      if (entry) {
        navigator.clipboard?.writeText(directorySnippet(entry, S.directory.source)).then(
          () => (cp.textContent = '已复制'),
          () => (cp.textContent = '复制失败，请手动选中'),
        );
      }
    }
    const f = e.target.closest('#board-filter [data-f]');
    if (f) {
      S.boardFilter = f.dataset.f;
      renderPanel();
    }
    if (e.target.closest('#perf-reset')) resetPerformance();
    const pv = e.target.closest('[data-preview]');
    if (pv && S.sid) showPreview(pv.dataset.preview);
    if (e.target.closest('[data-close]')) $('#viewer').close();
    if (e.target === $('#viewer')) $('#viewer').close();
  });
  $('#media').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || b.disabled) return;
    S.media = b.dataset.v || null;
    renderMediaChoice();
    scheduleEstimate();
  });
  $('#mediatier').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    S.mediaTier = b.dataset.v;
    renderMediaChoice();
    scheduleEstimate();
  });
  $('#workflow').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    S.workflow = b.dataset.v;
    try {
      localStorage.setItem(WORKFLOW_KEY, S.workflow);
    } catch {
      // 隐私模式等存不了：只是下次回到默认
    }
    renderComposer();
    renderLegend();
    scheduleEstimate();
  });
  // 预估区的建议按钮：讨论模式下题目需要多个媒体文件 → 一键切换到协同模式
  $('#estimate').addEventListener('click', (e) => {
    const b = e.target.closest('[data-switch-workflow]');
    if (!b) return;
    S.workflow = b.dataset.switchWorkflow;
    renderComposer();
    renderLegend();
    scheduleEstimate();
  });
  $('#anonymous').addEventListener('change', (e) => {
    S.anonymous = e.target.checked;
    renderLegend();
    scheduleEstimate();
  });
  $('#feed').addEventListener('click', (e) => {
    const opt = e.target.closest('[data-opt]');
    if (opt && !opt.disabled) answer(Number(opt.dataset.cp), opt.dataset.opt);
    if (e.target.closest('[data-act="resume"]')) doResume();
    const relay = e.target.closest('[data-carry]');
    if (relay) startCarry(relay.dataset.carry);
    const make = e.target.closest('[data-make]');
    if (make) startMake(make.dataset.make);
    if (e.target.closest('[data-act="export"]') && S.sid) exporter.open(S.sid);
    const quick = e.target.closest('.exports a[data-fmt]');
    if (quick) quick.href = exportHref(quick.dataset.sid, quick.dataset.fmt); // 用最新记住的样式
  });
  document.addEventListener('input', (e) => {
    if (e.target.id !== 'mdir-q') return;
    S.dirFilter = { ...S.dirFilter, q: e.target.value, limit: 80 };
    renderDirectoryList(); // 只重画列表，输入框不丢焦点
  });
  document.addEventListener('change', (e) => {
    if (e.target.id !== 'mdir-sort') return;
    S.dirFilter = { ...S.dirFilter, sort: e.target.value };
    renderDirectoryList();
  });
  $('#tabs').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    S.tab = b.dataset.t;
    if (S.tab === 'history') loadHistory();
    if (S.tab === 'contrib') loadContributions();
    if (S.tab === 'usage') loadBudget();
    if (S.tab === 'office') loadOffice();
    if (S.tab === 'plugins') loadBoard();
    if (S.tab === 'models') loadDirectory();
    renderPanel();
  });
  $('#pbody').addEventListener('click', (e) => {
    const row = e.target.closest('[data-sid]');
    if (row) open(row.dataset.sid);
  });
  $('#reveal').addEventListener('click', doReveal);
  $('#new').addEventListener('click', newQuestion);
  $('#toggle').addEventListener('click', (e) => {
    const main = $('#main');
    main.classList.toggle('collapsed');
    const open_ = !main.classList.contains('collapsed');
    e.currentTarget.setAttribute('aria-expanded', String(open_));
    e.currentTarget.textContent = open_ ? '收起面板' : '展开面板';
  });
}

async function init() {
  bind();
  try {
    S.status = await API.getStatus();
    setKindLabels(S.status.collab_kinds);
    S.budget = S.status.budget;
  } catch (e) {
    formError(`无法连接服务：${e.message}`);
  }
  let art = null;
  try {
    art = await API.members(); // 成员头像与小人；失败就当没有素材
  } catch {
    art = null;
  }
  setMemberArt(art);
  pet.setAllowed(false);
  pet.setMembers(art);
  home.setArt(art);
  gchat.setArt(art);
  hub.setArt(art);
  // 大立绘：读本机的立绘清单并抠好底（不阻塞其他页面），好了就让首页换成她说话
  standee.load().then(() => {
    hub.render();
    if (!$('#hub').hidden) standee.greet();
  });
  renderComposer();
  renderPicks();
  render();
  loadHistory();
  const m = location.hash.match(/s=([0-9a-f]+)/);
  if (m) await open(m[1]);
  const where = location.hash.match(/m=(\w+)/);
  if (where) setEntry(where[1]);
  else if (!m && landing() === 'hub') setEntry('hub');
  petReady = true;
  syncPet();
  (window.requestIdleCallback || setTimeout)(() => {
    warmFonts();
    // 自动化测试会在刚打开时检查哪些页面是藏着的，预热会短暂把页面放出来，测试里不预热
    if (!navigator.webdriver) warmViews();
  });
}

init();
