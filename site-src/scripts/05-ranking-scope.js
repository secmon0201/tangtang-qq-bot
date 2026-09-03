const scopeCopy = {
  current: "<strong>当前群排行</strong><br>NTE 薄荷/最强排行和鸣潮角色/练度排行都默认只比较当前群记录。",
  total: "<strong>机器人总排行</strong><br>只有显式写出总排行，NTE 或鸣潮才会比较糖糖保存的全部本地记录。"
};
const scopeExplain = document.querySelector("[data-scope-explain]");
document.querySelectorAll("[data-scope]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-scope]").forEach((item) => item.setAttribute("aria-selected", String(item === button)));
  if (scopeExplain) scopeExplain.innerHTML = scopeCopy[button.dataset.scope];
}));
