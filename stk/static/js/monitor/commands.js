/**
 * Monitor command registry. A command is {id, title, hotkey, scope, available, run}.
 * Hotkeys resolve through a focus stack: the top scope is asked first, then each one
 * below it, so a side panel can claim Escape while j/k still reach the focused panel.
 * The first available match wins; an unavailable command lets the key fall through.
 */
export function createCommands() {
  const commands = [];
  // [global, focused panel, ...overlays]
  const stack = ['global', null];

  // Text entry and selects (type-ahead) swallow keys; checkboxes and buttons must not.
  const TEXT_INPUTS = ['text', 'search', 'email', 'url', 'tel', 'password', 'number', 'date', 'datetime-local', 'month', 'time', 'week'];
  function typing(event) {
    const el = event.target;
    if (!el) return false;
    return el.isContentEditable || ['TEXTAREA', 'SELECT'].includes(el.tagName) || (el.tagName === 'INPUT' && TEXT_INPUTS.includes(el.type));
  }

  function key(event) {
    const mods = [event.ctrlKey && 'ctrl', event.metaKey && 'meta', event.altKey && 'alt'].filter(Boolean);
    return [...mods, event.key].join('+');
  }

  return {
    commands,
    stack,
    register(cmd) {
      commands.push({available: () => true, ...cmd});
    },
    focusPanel(scope) {
      stack[1] = scope;
    },
    push(scope) {
      if (stack[stack.length - 1] !== scope) stack.push(scope);
    },
    pop(scope) {
      const i = stack.lastIndexOf(scope);
      if (i > 1) stack.splice(i, 1);
    },
    resolve(hotkey) {
      for (let i = stack.length - 1; i >= 0; i--) {
        const found = commands.find(c => c.scope === stack[i] && c.hotkey === hotkey && c.available());
        if (found) return found;
      }
      return null;
    },
    handle(event) {
      if (typing(event)) return;
      const cmd = this.resolve(key(event));
      if (!cmd) return;
      event.preventDefault();
      cmd.run();
    },
  };
}
