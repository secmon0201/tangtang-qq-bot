const toast = document.querySelector("[data-toast]");
function showToast(text) {
  if (!toast) return;
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 1800);
}
document.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(button.dataset.copy); showToast(`已复制群号 ${button.dataset.copy}`); }
  catch { showToast(`群号：${button.dataset.copy}`); }
}));
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-command]");
  if (!button) return;
  const command = button.dataset.copyCommand;
  try { await navigator.clipboard.writeText(command); showToast(`已复制：${command}`); }
  catch { showToast(command); }
});
