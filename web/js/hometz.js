// 主时区：整个圆桌按哪个时区过日子。终端时钟里点星星选定，存在服务器（/api/home-tz），
// 公寓的天色和作息、群聊和朋友圈的时间、成员的作息都按它；「local」= 跟随这台设备。
// 本机再缓存一份，刷新时不闪；换了会发一个 window 事件「hometz」，各页面听到就重画。

const KEY = 'roundtable.hometz';
export const LOCAL = 'local';

const okZone = (tz) => {
  if (tz === LOCAL) return true;
  if (typeof tz !== 'string' || !tz) return false;
  try {
    new Intl.DateTimeFormat('en', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
};

let zone = (() => {
  try {
    const saved = localStorage.getItem(KEY);
    return okZone(saved) ? saved : LOCAL;
  } catch {
    return LOCAL;
  }
})();
let chosen = false;

export function homeZone() {
  return zone;
}

/** 给 toLocale*String / Intl.DateTimeFormat 的选项：主时区是「跟随设备」时不加。 */
export function tzOpt(extra = {}) {
  return zone === LOCAL ? { ...extra } : { ...extra, timeZone: zone };
}

/** 主时区在 date 这一刻的墙上时间：年月日、时分、星期（0 = 周一，和后端一致）。 */
export function homeParts(date = new Date()) {
  if (zone === LOCAL) {
    return { y: date.getFullYear(), m: date.getMonth() + 1, d: date.getDate(), h: date.getHours(), mi: date.getMinutes(), wd: (date.getDay() + 6) % 7 };
  }
  const fmt = new Intl.DateTimeFormat('en-US', tzOpt({ hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', weekday: 'short' }));
  const p = Object.fromEntries(fmt.formatToParts(date).map((x) => [x.type, x.value]));
  return {
    y: Number(p.year),
    m: Number(p.month),
    d: Number(p.day),
    h: Number(p.hour) % 24,
    mi: Number(p.minute),
    wd: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(p.weekday),
  };
}

/** 主时区里的「年-月-日」，用来判断是不是同一天。 */
export function homeDay(date = new Date()) {
  const p = homeParts(date);
  return `${p.y}-${p.m}-${p.d}`;
}

function adopt(next) {
  next = okZone(next) ? next : LOCAL;
  const changed = next !== zone;
  zone = next;
  try {
    localStorage.setItem(KEY, zone);
  } catch {
    // 存不了就只在这次生效
  }
  if (changed && typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('hometz', { detail: { zone } }));
  return changed;
}

/** 从服务器读主时区。返回 {zone, chosen}；读不到时用本机缓存。 */
export async function loadHomeZone() {
  try {
    const r = await fetch('/api/home-tz', { credentials: 'same-origin' });
    if (r.ok) {
      const d = await r.json();
      chosen = d.chosen === true;
      adopt(d.zone);
    }
  } catch {
    // 离线：沿用缓存
  }
  return { zone, chosen };
}

/** 设主时区：先本地生效（立即重画），再存到服务器。 */
export async function setHomeZone(next) {
  if (!okZone(next)) return false;
  adopt(next);
  chosen = true;
  try {
    const r = await fetch('/api/home-tz', {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ zone: next }),
    });
    return r.ok;
  } catch {
    return false;
  }
}
