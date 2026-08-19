const selectedRoot = document.querySelector("#selected-items"), availableRoot = document.querySelector("#available-items"), capacityStatus = document.querySelector("#capacity-status"), statusLine = document.querySelector("#settings-status"), saveButton = document.querySelector("#save-settings");
let candidates = new Map(), blocks = [], capacity = 6;
const costs = {large: 3, medium: 2, small: 1};
const itemLimits = {large: {hero: 6, strip: 6, compact: 5}, medium: {hero: 5, strip: 5, compact: 5}, small: {hero: 3, strip: 3, compact: 3}};
const esc = value => String(value ?? "").replace(/[&<>\"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"})[char]);
const used = () => blocks.reduce((total, block) => total + costs[block.size], 0);
const sourceItems = group => [...candidates.values()].filter(item => item.selectable && item.group === group);
const blockFor = id => blocks.find(block => block.block_id === id);
const patternFor = block => itemLimits[block.size]?.[block.layout_pattern] ? block.layout_pattern : "compact";
const limitFor = block => itemLimits[block.size]?.[patternFor(block)] || 3;
const itemLabel = item => item?.short_label || item?.label || "利用できない項目";
const defaultPrimaryFor = (group, items) => group === "一条パワコン" ? items.find(item => item.field === "load_power_w") || items[0] : items[0];

function trimBlockItems(block) {
  const limit = limitFor(block);
  if (block.item_ids.length <= limit) return;
  block.item_ids = [block.primary_item_id, ...block.item_ids.filter(id => id !== block.primary_item_id)].slice(0, limit);
}

function itemCheckbox(block, item) {
  const checked = block.item_ids.includes(item.id), full = block.item_ids.length >= limitFor(block);
  return `<label class="block-item-choice"><input type="checkbox" data-membership="${esc(block.block_id)}:${esc(item.id)}" ${checked ? "checked" : ""} ${!checked && full ? "disabled" : ""}><span>${esc(itemLabel(item))}</span></label>`;
}

function render() {
  const total = used();
  capacityStatus.textContent = `使用中 ${total} / ${capacity}${total >= capacity ? " — 表示領域がいっぱいです" : ""}`;
  selectedRoot.innerHTML = `<h2>表示中ブロック</h2>${blocks.length ? blocks.map((block, index) => {
    block.layout_pattern = patternFor(block); trimBlockItems(block);
    const items = sourceItems(block.group), limit = limitFor(block);
    return `<article class="display-block-editor" data-block-id="${esc(block.block_id)}"><header><input class="block-title-input" data-title="${esc(block.block_id)}" value="${esc(block.title)}" aria-label="ブロック名"><span>${esc(block.group)}</span></header><div class="block-controls"><label>サイズ<select data-size="${esc(block.block_id)}"><option value="large" ${block.size === "large" ? "selected" : ""}>大</option><option value="medium" ${block.size === "medium" ? "selected" : ""}>中</option><option value="small" ${block.size === "small" ? "selected" : ""}>小</option></select></label><label>表示形式<select data-layout="${esc(block.block_id)}"><option value="hero" ${block.layout_pattern === "hero" ? "selected" : ""}>主項目を大きく</option><option value="strip" ${block.layout_pattern === "strip" ? "selected" : ""}>均等に並べる</option><option value="compact" ${block.layout_pattern === "compact" ? "selected" : ""}>省スペース</option></select></label><label>主表示<select data-primary="${esc(block.block_id)}">${block.item_ids.map(itemId => { const item = candidates.get(itemId); return `<option value="${esc(itemId)}" ${itemId === block.primary_item_id ? "selected" : ""}>${esc(itemLabel(item))}</option>`; }).join("")}</select></label><span class="block-item-limit">最大${limit}項目</span><div class="display-setting-buttons"><button data-up="${esc(block.block_id)}" ${index === 0 ? "disabled" : ""}>上へ</button><button data-down="${esc(block.block_id)}" ${index === blocks.length - 1 ? "disabled" : ""}>下へ</button><button data-remove="${esc(block.block_id)}">削除</button></div></div><fieldset class="block-items"><legend>このブロックに表示する値（${block.item_ids.length} / ${limit}）</legend>${items.map(item => itemCheckbox(block, item)).join("") || "<p>表示可能な値がありません。</p>"}</fieldset></article>`;
  }).join("") : "<p>表示ブロックがありません。</p>"}`;
  const groups = [...new Set([...candidates.values()].filter(item => item.selectable).map(item => item.group))];
  availableRoot.innerHTML = `<h2>表示可能なデータ</h2>${groups.map(group => { const items = sourceItems(group); const alreadyAdded = blocks.some(block => block.group === group); const disabled = alreadyAdded || total >= capacity || !items.length; const state = alreadyAdded ? "追加済み" : total >= capacity ? "表示領域がいっぱいです" : "このデータでブロックを追加"; return `<section class="display-source-group"><h3>${esc(group)}</h3><div class="display-source-items">${items.map(item => `<span>${esc(itemLabel(item))}</span>`).join("")}</div><button class="add-block" data-add-block="${esc(group)}" ${disabled ? "disabled" : ""}>${state}</button></section>`; }).join("") || "<p>選択できる表示項目がありません。</p>"}`;
}

async function request(path, options = {}) { const response = await fetch(path, {headers: {"Content-Type":"application/json"}, ...options}); const payload = await response.json().catch(() => ({})); if (!response.ok) throw Error(payload.detail || "通信エラー"); return payload; }
async function load() { const [items, settings] = await Promise.all([request("/api/admin/display-items"), request("/api/admin/dashboard-settings")]); capacity = items.capacity; items.groups.forEach(group => group.items.forEach(item => candidates.set(item.id, item))); blocks = settings.presets.standard.blocks.map(block => ({...block, layout_pattern: block.layout_pattern || "compact"})); render(); }

selectedRoot.addEventListener("click", event => { const button = event.target.closest("button"); if (!button) return; const id = button.dataset.remove || button.dataset.up || button.dataset.down, index = blocks.findIndex(block => block.block_id === id); if (button.dataset.remove) blocks.splice(index, 1); if (button.dataset.up && index > 0) [blocks[index - 1], blocks[index]] = [blocks[index], blocks[index - 1]]; if (button.dataset.down && index >= 0 && index < blocks.length - 1) [blocks[index + 1], blocks[index]] = [blocks[index], blocks[index + 1]]; render(); });
selectedRoot.addEventListener("change", event => { const input = event.target, membership = input.dataset.membership || "", separator = membership.indexOf(":"), blockId = separator >= 0 ? membership.slice(0, separator) : "", itemId = separator >= 0 ? membership.slice(separator + 1) : "", block = blockFor(blockId); if (input.dataset.title) { const target = blockFor(input.dataset.title); target.title = input.value.trim() || target.group; } if (input.dataset.size) { const target = blockFor(input.dataset.size), old = target.size; target.size = input.value; if (used() > capacity) { target.size = old; statusLine.textContent = "表示領域がいっぱいです。"; } } if (input.dataset.layout) { const target = blockFor(input.dataset.layout); target.layout_pattern = input.value; trimBlockItems(target); } if (input.dataset.primary) blockFor(input.dataset.primary).primary_item_id = input.value; if (block && itemId) { if (input.checked && block.item_ids.length < limitFor(block)) block.item_ids.push(itemId); else if (!input.checked && block.item_ids.length > 1) { block.item_ids = block.item_ids.filter(id => id !== itemId); if (block.primary_item_id === itemId) block.primary_item_id = block.item_ids[0]; } } render(); });
availableRoot.addEventListener("click", event => { const button = event.target.closest("[data-add-block]"); if (!button || used() + 1 > capacity) return; const group = button.dataset.addBlock, items = sourceItems(group); if (!items.length || blocks.some(block => block.group === group)) return; const item = defaultPrimaryFor(group, items); blocks.push({block_id: `block_${Date.now()}_${blocks.length + 1}`, group, title: group, size: "small", layout_pattern: "compact", primary_item_id: item.id, item_ids: [item.id]}); render(); });
saveButton.addEventListener("click", async () => { saveButton.disabled = true; try { await request("/api/admin/dashboard-settings", {method: "PUT", body: JSON.stringify({version: 2, default_preset: "standard", presets: {standard: {blocks}}})}); statusLine.textContent = "保存しました"; } catch (error) { statusLine.textContent = error.message; } finally { saveButton.disabled = false; } });
load().catch(error => { statusLine.textContent = error.message; });
