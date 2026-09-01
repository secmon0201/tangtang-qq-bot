const scopeCopy = {
  current: "<strong>当前群排行</strong><br>薄荷排行和最强排行默认只比较当前群记录。",
  total: "<strong>机器人总排行</strong><br>只有薄荷总排行和最强总排行会比较糖糖记录到的全部群。"
};
const scopeExplain = document.querySelector("[data-scope-explain]");
document.querySelectorAll("[data-scope]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-scope]").forEach((item) => item.setAttribute("aria-selected", String(item === button)));
  if (scopeExplain) scopeExplain.innerHTML = scopeCopy[button.dataset.scope];
}));
