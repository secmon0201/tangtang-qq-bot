const dialog = document.querySelector("[data-modal-dialog]");
const modalTitle = document.querySelector("[data-modal-title]");
const modalBody = document.querySelector("[data-modal-body]");
let modalSource = null;
document.querySelectorAll("[data-modal]").forEach((button) => button.addEventListener("click", (event) => {
  if (!dialog || !modalTitle || !modalBody) return;
  event.preventDefault();
  const entry = modalContent[button.dataset.modal];
  if (!entry) return;
  modalSource = button;
  modalTitle.textContent = entry.title;
  const commands = entry.commands || (entry.command ? [entry.command] : []);
  const examples = entry.examples || [];
  modalBody.innerHTML = `<p class="modal-intro">${entry.text}</p><div class="modal-list">${entry.items.map((item) => `<div>${item}</div>`).join("")}</div>${examples.length ? `<section class="modal-examples"><h3>怎么用</h3>${examples.map((item) => `<p>${item}</p>`).join("")}</section>` : ""}${commands.length ? `<section class="modal-commands"><h3>常用指令</h3><div>${commands.map((command) => `<button type="button" data-copy-command="${command}"><code>${command}</code><i data-lucide="copy"></i></button>`).join("")}</div></section>` : ""}${entry.availability ? `<aside class="modal-availability"><i data-lucide="shield-alert"></i><p><strong>生效条件</strong>${entry.availability}</p></aside>` : ""}`;
  window.lucide?.createIcons();
  dialog.showModal();
  document.body.classList.add("modal-open");
}));

function closeDialog() {
  if (!dialog?.open) return;
  dialog.close();
  document.body.classList.remove("modal-open");
  modalSource?.focus();
}
document.querySelector("[data-modal-close]")?.addEventListener("click", closeDialog);
dialog?.addEventListener("click", (event) => {
  const box = dialog.getBoundingClientRect();
  if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) closeDialog();
});
dialog?.addEventListener("close", () => document.body.classList.remove("modal-open"));
