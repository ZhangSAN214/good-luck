// 匿名代号（塔罗牌 22 张大阿卡纳）的简笔画头像：极细线条，和界面图标同一套画法。
// 只按代号的名字画，不含任何身份信息；代号池本身在 config/personas.yaml。

const P = {
  愚者: '<circle cx="12" cy="6" r="2.5"/><path d="M12 8.5v6M8 11l4-1 4 1M12 14.5l-3 5M12 14.5l3 5M17 4l2-1"/>',
  魔术师: '<path d="M6 12c0-2 1.5-3 3-3s2 1 3 3 1.5 3 3 3 3-1 3-3-1.5-3-3-3-2 1-3 3-1.5 3-3 3-3-1-3-3z"/><path d="M12 3v3M12 18v3"/>',
  女祭司: '<path d="M14.5 4a7 7 0 1 0 0 16 8 8 0 0 1 0-16z"/><path d="M18 9v6"/>',
  皇后: '<circle cx="12" cy="9" r="4"/><path d="M12 13v8M9 18h6"/>',
  皇帝: '<path d="M5 20V9l7-5 7 5v11M9 20v-6h6v6M5 13h14"/>',
  教皇: '<path d="M12 3v18M8 7h8M7 11h10M9 15h6"/>',
  恋人: '<path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.5-7 10-7 10z"/>',
  战车: '<path d="M4 15h16M6 15V9h12v6"/><circle cx="8" cy="18" r="2"/><circle cx="16" cy="18" r="2"/>',
  力量: '<path d="M6 12a3 3 0 1 1 6 0 3 3 0 1 0 6 0"/><path d="M5 19c2-2 4-3 7-3s5 1 7 3"/>',
  隐者: '<path d="M12 3v3M9 6h6l-1 5h-4z"/><path d="M12 11v10M8 21h8"/>',
  命运之轮: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/><path d="M12 4v6M12 14v6M4 12h6M14 12h6"/>',
  正义: '<path d="M12 3v18M6 21h12M5 7h14"/><path d="M5 7l-2 6h4zM19 7l-2 6h4z"/>',
  倒吊人: '<path d="M4 4h16M12 4v6"/><circle cx="12" cy="17" r="2.5"/><path d="M12 10v4.5M9 12h6"/>',
  死神: '<path d="M6 21V9a6 6 0 0 1 12 0v12"/><path d="M9 11h0M15 11h0M10 16h4"/>',
  节制: '<path d="M5 6h5v5H5zM14 13h5v5h-5z"/><path d="M10 9c3 0 4 2 4 5"/>',
  恶魔: '<path d="M6 4l3 5M18 4l-3 5"/><circle cx="12" cy="13" r="5"/><path d="M10 13h0M14 13h0"/>',
  高塔: '<path d="M8 21V7h8v14M7 7l1-3h8l1 3M6 21h12"/><path d="M15 3l-2 4 2 1-2 4"/>',
  星星: '<path d="M12 3l2.4 5.6 6 .5-4.6 4 1.4 6L12 16l-5.2 3.1 1.4-6-4.6-4 6-.5z"/>',
  月亮: '<path d="M15 3a8.5 8.5 0 1 0 6 13A7 7 0 0 1 15 3z"/>',
  太阳: '<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/>',
  审判: '<path d="M4 10l9-4v10l-9-4zM13 8h3a3 3 0 0 1 0 6h-3"/><path d="M6 13l1 6"/>',
  世界: '<ellipse cx="12" cy="12" rx="6" ry="8"/><circle cx="12" cy="12" r="2.5"/>',
};

const ORDER = Object.keys(P);
const ROMAN = ['0', 'Ⅰ', 'Ⅱ', 'Ⅲ', 'Ⅳ', 'Ⅴ', 'Ⅵ', 'Ⅶ', 'Ⅷ', 'Ⅸ', 'Ⅹ', 'Ⅺ', 'Ⅻ', 'XIII', 'XIV', 'XV', 'XVI', 'XVII', 'XVIII', 'XIX', 'XX', 'XXI'];

/** 代号对应的简笔画 SVG；不是塔罗牌代号时返回空串。 */
export function tarotIcon(code) {
  const p = P[code];
  return p ? `<svg class="tarot" viewBox="0 0 24 24" aria-hidden="true">${p}</svg>` : '';
}

/** 牌序罗马数字（Ⅰ 魔术师）；不是塔罗牌代号时返回空串。 */
export function tarotNumeral(code) {
  const i = ORDER.indexOf(code);
  return i < 0 ? '' : ROMAN[i];
}
