(() => {
  if (window.xiaozhiShell) return;
  const root = document.body;
  let returnFocus = null;
  const narrow = window.matchMedia('(max-width: 1100px)');
  function sync() {
    const open = root.classList.contains('inspector-open');
    document.querySelectorAll('[data-shell="inspector"]').forEach(button => {
      button.setAttribute('aria-expanded', String(open));
    });
    document.querySelectorAll('[data-shell="sidebar"]').forEach(button => {
      button.setAttribute('aria-expanded', String(root.classList.contains('sidebar-open')));
    });
    window.dispatchEvent(new Event('resize'));
  }
  function open() {
    if (root.classList.contains('inspector-open')) return;
    returnFocus = document.activeElement;
    root.classList.add('inspector-open');
    if (narrow.matches) root.classList.remove('sidebar-open');
    sync();
  }
  function close() {
    root.classList.remove('inspector-open', 'sidebar-open');
    sync();
    if (returnFocus?.isConnected) returnFocus.focus();
  }
  window.xiaozhiShell = {open, close};
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-shell], [data-workbench-tab]');
    if (!button) return;
    if (button.dataset.workbenchTab) {
      open();
      if (!window.xiaozhiOpenTab?.(button.dataset.workbenchTab)) {
        window.xiaozhiAppearance?.notify('面板正在加载，请稍后重试。');
      }
    } else if (button.dataset.shell === 'close') close();
    else if (button.dataset.shell === 'inspector') {
      if (root.classList.contains('inspector-open')) close(); else open();
    } else if (button.dataset.shell === 'sidebar') {
      if (!root.classList.contains('sidebar-open')) returnFocus = document.activeElement;
      root.classList.toggle('sidebar-open');
      root.classList.remove('inspector-open');
      sync();
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && (root.classList.contains('inspector-open') || root.classList.contains('sidebar-open'))) {
      close();
    }
  });
  narrow.addEventListener('change', close);
  sync();
})();

