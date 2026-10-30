// 表情：Emoji 分类 + 三套自绘动态表情包（团子、猫猫、文字）。全部是内联 SVG + CSS 动画，不用外部图片。
// 另有成员专属表情（setMemberPacks 装入，是后端给的图片）。
// 表情包在消息里写成文字记号「[表情:团子·开心]」：成员看得懂（是一段文字），界面把它画成动态表情。

export const EMOJI = [
  { id: 'face', name: '笑脸', icon: '😊', list: '😀 😃 😄 😁 😆 😅 🤣 😂 🙂 😉 😊 😇 🥰 😍 🤩 😘 😋 😛 😜 🤪 😝 🤗 🤭 🤫 🤔 🫡 🤐 🤨 😐 😑 😶 🫥 😏 😒 🙄 😬 😮‍💨 😌 😔 😪 🤤 😴 😷 🤒 🥵 🥶 🥴 😵 🤯 🥳 😎 🤓 🧐 😕 🫤 😟 🙁 😮 😯 😲 😳 🥺 🥹 😦 😧 😨 😰 😥 😢 😭 😱 😖 😣 😞 😓 😩 😫 🥱 😤 😡 😠 🤬 😈 💀 🤡 👻 👽 🤖' },
  { id: 'hand', name: '手势', icon: '👍', list: '👍 👎 👌 🤌 🤏 ✌️ 🤞 🫰 🤟 🤘 🤙 👈 👉 👆 👇 ☝️ ✋ 🤚 🖐️ 🖖 👋 🫶 👏 🙌 👐 🤲 🤝 🙏 ✍️ 💪 🫵 🫂 💅 🤳 🙇 🙋 🙆 🙅 🤷 🤦 💃 🕺 🏃 🧘' },
  { id: 'love', name: '心情', icon: '❤️', list: '❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💔 ❤️‍🔥 💕 💞 💓 💗 💖 💘 💝 💟 ✨ 💫 ⭐ 🌟 🔥 💥 💢 💦 💨 💤 💬 💭 🎉 🎊 🎈 🎁 🏆 🥇 💯 ✅ ❌ ❓ ❗ ⚡ 🌈 ☀️ 🌙 ☁️ 🌧️ ❄️' },
  { id: 'animal', name: '动物', icon: '🐱', list: '🐶 🐱 🐭 🐹 🐰 🦊 🐻 🐼 🐻‍❄️ 🐨 🐯 🦁 🐮 🐷 🐸 🐵 🙈 🙉 🙊 🐔 🐧 🐦 🐤 🦆 🦉 🦄 🐝 🦋 🐌 🐞 🐢 🐍 🐙 🦑 🦀 🐠 🐬 🐳 🦈 🐊 🦒 🦘 🐿️ 🦔 🌸 🌻 🌹 🍀 🌵 🍁' },
  { id: 'food', name: '吃喝', icon: '🍜', list: '🍎 🍊 🍋 🍉 🍇 🍓 🫐 🍑 🥭 🍍 🥝 🍅 🥑 🌽 🥕 🍞 🥐 🧀 🍳 🥞 🍔 🍟 🍕 🌭 🌮 🍣 🍱 🥟 🍜 🍲 🍛 🍙 🍚 🍢 🍡 🍧 🍦 🍰 🎂 🍮 🍩 🍪 🍫 🍬 🍭 🧋 ☕ 🍵 🥤 🍺 🍷 🥂' },
  { id: 'play', name: '生活', icon: '🎮', list: '⚽ 🏀 🏈 ⚾ 🎾 🏐 🏓 🏸 🥊 🎯 🎳 🎮 🕹️ 🎲 🧩 🎨 🎬 🎤 🎧 🎵 🎶 🎹 🎸 🎻 📚 📖 ✏️ 📝 💻 📱 📷 🎥 💡 🕯️ 🛋️ 🛏️ 🚗 🚌 🚲 ✈️ 🚀 🏠 🏫 🏖️ ⛰️ 🗺️ 🧳 🛒 💰 🎓' },
].map((c) => ({ ...c, list: c.list.split(' ') }));

// --- 自绘表情 -------------------------------------------------------------------

const EYES = {
  dot: '<circle cx="44" cy="62" r="5"/><circle cx="76" cy="62" r="5"/><circle cx="46" cy="60" r="1.6" fill="#fff"/><circle cx="78" cy="60" r="1.6" fill="#fff"/>',
  happy: '<path d="M37 64q7-10 14 0M69 64q7-10 14 0" class="ln"/>',
  cry: '<path d="M37 60q7 7 14 0M69 60q7 7 14 0" class="ln"/>',
  angry: '<path d="M35 52l15 6M85 52l-15 6" class="ln"/><circle cx="44" cy="64" r="4.5"/><circle cx="76" cy="64" r="4.5"/>',
  wide: '<circle cx="44" cy="61" r="9" fill="#fff" class="ln th"/><circle cx="76" cy="61" r="9" fill="#fff" class="ln th"/><circle cx="44" cy="62" r="3.6"/><circle cx="76" cy="62" r="3.6"/>',
  sleep: '<path d="M37 63h14M69 63h14" class="ln"/>',
  wink: '<circle cx="44" cy="62" r="5"/><path d="M69 63q7-8 14 0" class="ln"/>',
  dizzy: '<path d="M38 56l12 12M50 56L38 68M70 56l12 12M82 56L70 68" class="ln"/>',
  star: '<path d="M44 53l2.6 6 6.4.6-4.9 4.2 1.5 6.3L44 66.8l-5.6 3.3 1.5-6.3-4.9-4.2 6.4-.6z" fill="#ffd25e" class="ln th"/><path d="M76 53l2.6 6 6.4.6-4.9 4.2 1.5 6.3L76 66.8l-5.6 3.3 1.5-6.3-4.9-4.2 6.4-.6z" fill="#ffd25e" class="ln th"/>',
  side: '<circle cx="48" cy="62" r="5"/><circle cx="80" cy="62" r="5"/><path d="M36 54h14M68 54h14" class="ln th"/>',
  sad: '<path d="M36 55l12-4M84 55l-12-4" class="ln th"/><circle cx="44" cy="64" r="4.5"/><circle cx="76" cy="64" r="4.5"/>',
};
const MOUTH = {
  smile: '<path d="M52 76q8 8 16 0" class="ln"/>',
  open: '<path d="M50 74q10 16 20 0z" fill="#7a2f3a" class="ln th"/><path d="M54 80q6 4 12 0" fill="#ff8fa0"/>',
  o: '<ellipse cx="60" cy="80" rx="5" ry="6.5" fill="#7a2f3a" class="ln th"/>',
  wavy: '<path d="M48 80q4-4 8 0t8 0 8 0" class="ln"/>',
  flat: '<path d="M53 79h14" class="ln"/>',
  cat: '<path d="M50 76q5 5 10 0q5 5 10 0" class="ln"/>',
  tongue: '<path d="M51 75q9 8 18 0" class="ln"/><path d="M58 78q4 9 8 0" fill="#ff7d93" class="ln th"/>',
  frown: '<path d="M52 82q8-8 16 0" class="ln"/>',
  grin: '<path d="M47 73h26q-2 13-13 13t-13-13z" fill="#fff" class="ln th"/><path d="M47 73h26" class="ln th"/>',
  chew: '<path d="M52 77q4 3 8 0q4 3 8 0" class="ln"/>',
};
const EXTRA = {
  blush: '<g class="fx-blush"><ellipse cx="34" cy="74" rx="7" ry="4" fill="#ff8aa6" opacity=".55"/><ellipse cx="86" cy="74" rx="7" ry="4" fill="#ff8aa6" opacity=".55"/></g>',
  tears: '<g class="fx-tear"><path d="M40 68q-4 8 0 12q4-4 0-12z" fill="#7cc8ff"/><path d="M80 68q-4 8 0 12q4-4 0-12z" fill="#7cc8ff"/></g>',
  river: '<g class="fx-river"><path d="M40 66v34" stroke="#7cc8ff" stroke-width="5" stroke-linecap="round"/><path d="M80 66v34" stroke="#7cc8ff" stroke-width="5" stroke-linecap="round"/></g>',
  sweat: '<path class="fx-sweat" d="M92 36q-6 10 0 14q6-4 0-14z" fill="#8fd3ff"/>',
  heart: '<g class="fx-heart"><path d="M96 26c-4-7-14-3-10 5l10 9 10-9c4-8-6-12-10-5z" fill="#ff5c7a"/></g>',
  hearts: '<g class="fx-hearts"><path d="M24 30c-3-5-10-2-7 3l7 7 7-7c3-5-4-8-7-3z" fill="#ff5c7a"/><path d="M98 22c-3-5-10-2-7 3l7 7 7-7c3-5-4-8-7-3z" fill="#ff8fb0"/></g>',
  zzz: '<g class="fx-zzz" fill="none"><path d="M84 22h10l-10 10h10M98 8h7l-7 7h7" class="ln th"/></g>',
  ask: '<g class="fx-ask"><path d="M92 18q0-8 8-8t8 8q0 5-8 8v4" class="ln"/><circle cx="100" cy="37" r="2.4"/></g>',
  bang: '<g class="fx-bang"><path d="M100 10v18" class="ln"/><circle cx="100" cy="35" r="2.6"/></g>',
  anger: '<g class="fx-anger" stroke="#ff4d4d" stroke-width="4" stroke-linecap="round" fill="none"><path d="M90 20q4 4 0 8M100 20q-4 4 0 8M90 32q4-4 8 0M90 16q4 4 8 0"/></g>',
  sparkle: '<g class="fx-spark" fill="#ffd25e"><path d="M18 30l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/><path d="M100 18l1.5 4.5 4.5 1.5-4.5 1.5-1.5 4.5-1.5-4.5-4.5-1.5 4.5-1.5z"/><path d="M104 84l1.4 4 4 1.4-4 1.4-1.4 4-1.4-4-4-1.4 4-1.4z"/></g>',
  thumb: '<g class="fx-thumb"><path d="M86 88h8v-14q0-6 5-8l2 9h7q4 0 3 4l-3 12q-1 3-4 3H94" fill="#ffd9a8" class="ln th"/><rect x="82" y="84" width="10" height="16" rx="2" fill="#f2a65a" class="ln th"/></g>',
  melon: '<g class="fx-melon"><path d="M72 90a20 20 0 0 0 36 0z" fill="#ff6b6b" class="ln th"/><path d="M72 90a20 20 0 0 0 36 0" stroke="#4caf50" stroke-width="4" fill="none"/><circle cx="84" cy="94" r="1.4"/><circle cx="92" cy="98" r="1.4"/><circle cx="98" cy="93" r="1.4"/></g>',
  fish: '<g class="fx-fish"><path d="M78 96q12-12 26 0q-14 12-26 0zM104 96l8-6v12z" fill="#7cc8ff" class="ln th"/><circle cx="86" cy="94" r="1.6"/></g>',
  flag: '<g class="fx-flag"><path d="M96 20v52" class="ln th"/><path d="M96 22h22l-6 8 6 8H96z" fill="#ff5c5c" class="ln th"/></g>',
  ok: '<g class="fx-ok"><circle cx="100" cy="28" r="12" fill="#5ccf8a" class="ln th"/><path d="M94 28l4 4 8-8" stroke="#fff" stroke-width="3.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>',
  sweatdrop: '<path class="fx-sweat" d="M88 44q-5 9 0 12q5-3 0-12z" fill="#8fd3ff"/>',
  shine: '<g class="fx-shine"><path d="M60 6v10M30 14l6 7M90 14l-6 7" class="ln th" stroke="#ffd25e"/></g>',
  note: '<g class="fx-note"><path d="M98 14v18" class="ln th"/><ellipse cx="94" cy="33" rx="5" ry="4"/><path d="M98 14l10 3v6l-10-3" class="ln th"/></g>',
  boba: '<g class="fx-boba"><path d="M84 70h22l-3 30h-16z" fill="#e8c9a0" class="ln th"/><path d="M98 70l4-14" class="ln th"/><circle cx="91" cy="94" r="2"/><circle cx="97" cy="96" r="2"/><circle cx="94" cy="90" r="2"/></g>',
};

const BODY = {
  blob: (c) =>
    `<ellipse cx="60" cy="108" rx="34" ry="5" fill="#000" opacity=".16" class="shadow"/><path class="ln th" fill="${c}" d="M60 30c26 0 46 18 46 42 0 22-18 32-46 32S14 94 14 72c0-24 20-42 46-42z"/><path d="M34 46q8-8 18-9" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".55" fill="none"/>`,
  cat: (c) =>
    `<ellipse cx="60" cy="108" rx="34" ry="5" fill="#000" opacity=".16" class="shadow"/><path class="ln th" fill="${c}" d="M22 40l6-24 22 16q10-3 20 0l22-16 6 24q8 12 8 30c0 22-18 34-46 34S14 92 14 70q0-18 8-30z"/><path d="M28 24l4 12 8-6zM92 24l-4 12-8-6z" fill="#ffb3c4"/><path d="M14 74l-10-2M14 80l-10 2M106 74l10-2M106 80l10 2" class="ln th" opacity=".7"/>`,
};

const STK_PACKS = [
  {
    id: '团子',
    body: 'blob',
    color: '#ffe3b8',
    items: [
      ['开心', 'happy', 'grin', ['blush', 'sparkle'], 'bounce'],
      ['哭哭', 'cry', 'frown', ['river'], 'sob'],
      ['生气', 'angry', 'frown', ['anger'], 'shake', '#ffb3a3'],
      ['害羞', 'happy', 'wavy', ['blush', 'hearts'], 'sway', '#ffd3dd'],
      ['震惊', 'wide', 'o', ['bang'], 'pop'],
      ['晚安', 'sleep', 'smile', ['zzz'], 'breathe', '#d9e4ff'],
      ['比心', 'wink', 'tongue', ['heart'], 'jelly'],
      ['疑惑', 'side', 'flat', ['ask'], 'tilt'],
      ['点赞', 'happy', 'smile', ['thumb'], 'nod'],
      ['摸鱼', 'sleep', 'cat', ['fish'], 'sway', '#cdeee0'],
      ['冲鸭', 'star', 'open', ['flag'], 'dash', '#ffe08a'],
      ['吃瓜', 'dot', 'chew', ['melon'], 'nod'],
      ['流汗', 'dot', 'flat', ['sweat'], 'tilt'],
      ['晕了', 'dizzy', 'wavy', [], 'spin'],
      ['收到', 'happy', 'smile', ['ok'], 'pop', '#d6f5e1'],
      ['奶茶', 'happy', 'chew', ['boba', 'blush'], 'bounce', '#f3dcc2'],
    ],
  },
  {
    id: '猫猫',
    body: 'cat',
    color: '#f2c38b',
    items: [
      ['喵', 'dot', 'cat', ['note'], 'bounce'],
      ['蹭蹭', 'happy', 'cat', ['blush', 'hearts'], 'rub'],
      ['委屈', 'sad', 'frown', ['tears'], 'shiver', '#e6d3c2'],
      ['炸毛', 'wide', 'open', ['anger'], 'shake', '#d9a066'],
      ['睡觉', 'sleep', 'cat', ['zzz'], 'breathe', '#c9c9d9'],
      ['偷看', 'side', 'flat', ['sweatdrop'], 'peek'],
      ['得意', 'wink', 'tongue', ['shine'], 'tilt', '#ffd59a'],
      ['饿了', 'dot', 'o', ['ask'], 'nod', '#f0e0d0'],
      ['开心', 'happy', 'grin', ['sparkle'], 'jelly', '#ffe0a8'],
      ['哈？', 'wide', 'flat', ['bang'], 'pop', '#bfbfbf'],
      ['贴贴', 'happy', 'smile', ['heart', 'blush'], 'sway', '#fff0e0'],
      ['躺平', 'sleep', 'flat', [], 'melt', '#e8d6b8'],
    ],
  },
];

const WORDS = [
  ['收到！', 'stamp', '#1f8a5b', '#d7f6e6'],
  ['好耶', 'jump', '#ff7a45', '#fff1e0'],
  ['哈哈哈', 'wave', '#ffb400', '#fff8db'],
  ['在吗', 'blink', '#4c7dff', '#e2ebff'],
  ['晚安', 'glow', '#8a7dff', '#1d1b3a'],
  ['冲！', 'dash', '#ff3d5a', '#ffe3e8'],
  ['谢谢', 'float', '#e0568f', '#ffe6f1'],
  ['绝了', 'neon', '#2ee6d6', '#0e2a2c'],
  ['救命', 'shake', '#ff4d4d', '#fff0f0'],
  ['6', 'spin', '#111', '#ffe14d'],
  ['离谱', 'tilt', '#7c4dff', '#efe8ff'],
  ['抱抱', 'beat', '#ff6f91', '#fff0f4'],
];

export const STICKERS = [
  ...STK_PACKS.map((p) => ({
    id: p.id,
    items: p.items.map(([name, eyes, mouth, extras, anim, color]) => ({ name, eyes, mouth, extras, anim, color: color || p.color, body: p.body })),
  })),
  { id: '文字', items: WORDS.map(([name, anim, ink, bg]) => ({ name, anim, ink, bg, word: true })) },
];

/** 成员专属表情（审过的那些，后端 /api/chat/stickers）：套装名是成员昵称，每张是一张图。重复装入时先换掉旧的。 */
export function setMemberPacks(packs) {
  for (let i = STICKERS.length - 1; i >= 0; i--) if (STICKERS[i].member) STICKERS.splice(i, 1);
  for (const p of packs || []) {
    if (!p?.pack || !p.items?.length || STICKERS.some((x) => x.id === p.pack)) continue;
    STICKERS.push({ id: p.pack, member: p.member, items: p.items.map((x) => ({ name: x.name, caption: x.caption, img: x.url })) });
  }
}

export const token = (pack, name) => `[表情:${pack}·${name}]`;
export const TOKEN_RE = /\[表情:([^\]·]{1,6})·([^\]]{1,6})\]/g;

export function findSticker(pack, name) {
  return STICKERS.find((p) => p.id === pack)?.items.find((s) => s.name === name) || null;
}

const escAttr = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

function wordSvg(s) {
  const chars = [...s.name];
  const size = chars.length <= 1 ? 64 : chars.length === 2 ? 44 : 34;
  const span = chars.length * size * 0.92;
  const start = 60 - span / 2 + (size * 0.92) / 2;
  const letters = chars
    .map((ch, i) => `<text class="wd-ch" style="--i:${i}" x="${(start + i * size * 0.92).toFixed(1)}" y="${70 + size * 0.1}" font-size="${size}">${escAttr(ch)}</text>`)
    .join('');
  return `<g class="stk-body"><rect x="8" y="26" width="104" height="68" rx="22" fill="${s.bg}" stroke="${s.ink}" stroke-width="3"/><rect x="14" y="32" width="92" height="56" rx="17" fill="none" stroke="${s.ink}" stroke-width="1.2" stroke-dasharray="3 4" opacity=".5"/>
    <g fill="${s.ink}" text-anchor="middle" font-weight="900">${letters}</g></g>`;
}

/** 画一个表情包。big：消息里单独发的大号表情；否则是行内小号。 */
export function stickerSVG(pack, name, { big = false, label = true } = {}) {
  const s = findSticker(pack, name);
  if (!s) return null;
  if (s.img) {
    const alt = label ? escAttr(`${pack}：${s.caption || name}`) : '';
    return `<img class="stk pic${big ? ' big' : ''}" src="${escAttr(s.img)}" alt="${alt}" title="${escAttr(s.caption || name)}" loading="lazy" decoding="async">`;
  }
  const inner = s.word
    ? wordSvg(s)
    : `<g class="stk-body">${BODY[s.body](s.color)}<g class="face">${EYES[s.eyes] || ''}${MOUTH[s.mouth] || ''}</g></g>${s.extras.map((x) => EXTRA[x] || '').join('')}`;
  const aria = label ? ` role="img" aria-label="${escAttr(`${pack}：${name}`)}"` : ' aria-hidden="true"';
  return `<svg class="stk${big ? ' big' : ''}${s.word ? ' word' : ''}" data-anim="${s.anim}" viewBox="0 0 120 120"${aria}><title>${escAttr(name)}</title>${inner}</svg>`;
}

/** 文字里是否只有一个表情包记号（聊天里单独发表情时显示成大号、没有气泡） */
export function soloSticker(text) {
  const m = /^\s*\[表情:([^\]·]{1,6})·([^\]]{1,6})\]\s*$/.exec(text || '');
  return m && findSticker(m[1], m[2]) ? { pack: m[1], name: m[2] } : null;
}

// --- 最近使用（只存这台电脑的浏览器） ------------------------------------------------

const RECENT_KEY = 'roundtable.chat.recent-stickers';

export function recent() {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, 24) : [];
  } catch {
    return [];
  }
}

export function remember(item) {
  try {
    const list = [item, ...recent().filter((x) => x !== item)].slice(0, 24);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  } catch {
    /* 存不了就算了 */
  }
}
