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
  modalBody.innerHTML = `<p>${entry.text}</p><div class="modal-list">${entry.items.map((item) => `<div>${item}</div>`).join("")}</div><div class="modal-command">${entry.command}</div>`;
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
