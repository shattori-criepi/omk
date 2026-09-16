window.createSoftwareKeyboard = function createSoftwareKeyboard(elements, options) {
  let state = null;
  const keyAllowed = options.keyAllowed || (() => true);
  const normalize = options.normalize || (value => value);
  const format = options.format || (value => value);
  const rawPosition = options.rawPosition || ((_, position) => position);
  const displayPosition = options.displayPosition || ((_, position) => position);

  function target() { return state?.target?.() || null; }
  function renderState() {
    if (!state) return;
    const input = target();
    if (!input || input.disabled) { close(false); return; }
    elements.title.textContent = state.label;
    elements.count.textContent = `${state.value.length} / ${state.length}文字`;
    elements.value.textContent = format(state.value, state.length) || "入力してください";
    input.value = format(state.value, state.length);
    const position = displayPosition(state.value, state.selectionStart, state.length);
    input.setSelectionRange?.(position, displayPosition(state.value, state.selectionEnd, state.length));
  }
  function insert(key) {
    if (!state || !keyAllowed(key) || state.value.length >= state.length) return;
    const before = state.value.slice(0, state.selectionStart), after = state.value.slice(state.selectionEnd);
    state.value = (before + key + after).slice(0, state.length);
    state.selectionStart = state.selectionEnd = Math.min(before.length + key.length, state.length);
    renderState();
  }
  function backspace() {
    if (!state) return;
    if (state.selectionStart !== state.selectionEnd) state.value = state.value.slice(0, state.selectionStart) + state.value.slice(state.selectionEnd);
    else if (state.selectionStart > 0) { state.value = state.value.slice(0, state.selectionStart - 1) + state.value.slice(state.selectionEnd); state.selectionStart -= 1; }
    state.selectionEnd = state.selectionStart;
    renderState();
  }
  function clear() { if (!state) return; state.value = ""; state.selectionStart = state.selectionEnd = 0; renderState(); }
  function close(restore) {
    if (!state) return;
    const closedState = state;
    const input = target();
    if (restore && input) input.value = format(closedState.original, closedState.length);
    state = null;
    elements.overlay.hidden = true;
    if (restore) closedState.onCancel?.(closedState);
  }
  function confirm() {
    if (!state) return;
    const confirmedState = state;
    close(false);
    confirmedState.onConfirm?.(confirmedState.value, confirmedState);
  }
  function renderKeys() {
    elements.keys.textContent = "";
    const rows = options.keyRows(state);
    for (const [rowIndex, row] of rows.entries()) {
      const rowElement = document.createElement("div");
      rowElement.className = "broute-keyboard-row";
      if (rowIndex === rows.length - 1 && options.lastRowClass) rowElement.className += ` ${options.lastRowClass}`;
      const controls = rowIndex === rows.length - 1 ? [...(options.extraActions?.(state) || []), ["⌫", backspace, "broute-keyboard-backspace"], ...(options.showClear === false ? [] : [["全消去", clear, "broute-keyboard-clear"]])] : [];
      rowElement.style?.setProperty("--key-count", String(row.length + controls.length));
      for (const key of row) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = key;
        button.addEventListener("click", () => insert(key));
        rowElement.append(button);
      }
      if (rowIndex === rows.length - 1) {
        for (const [label, action, className] of controls) {
          const button = document.createElement("button");
          button.type = "button"; button.className = className; button.textContent = label;
          button.addEventListener("click", action); rowElement.append(button);
        }
      }
      elements.keys.append(rowElement);
    }
  }
  function open(input, config) {
    if (input.disabled) return;
    const value = normalize(input.value).slice(0, config.length);
    const start = config.appendAtEnd ? value.length : rawPosition(input.value, input.selectionStart ?? input.value.length);
    const end = config.appendAtEnd ? value.length : rawPosition(input.value, input.selectionEnd ?? input.selectionStart ?? input.value.length);
    state = {target: config.target || (() => input), label: config.label, length: config.length, value, original: value,
             selectionStart: start, selectionEnd: end, uppercase: false, onConfirm: config.onConfirm, onCancel: config.onCancel};
    elements.overlay.hidden = false;
    renderKeys(); renderState();
  }
  elements.cancel.addEventListener("click", () => close(true));
  elements.confirm.addEventListener("click", confirm);
  document.addEventListener("keydown", event => {
    if (!state) return;
    if (event.key === "Backspace") { event.preventDefault(); backspace(); }
    else if (event.key === "Enter") { event.preventDefault(); confirm(); }
    else if (event.key === "Escape") { event.preventDefault(); close(true); }
    else if (keyAllowed(event.key)) { event.preventDefault(); insert(event.key); }
  });
  return {open, insert, backspace, clear, close, refresh: () => { if (!state) return; renderKeys(); renderState(); }};
};
